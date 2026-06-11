"""
agents/cash_flow_agent.py
Agent 3.5 — Cash Flow Agent
Wave 3 (dependent): Runs AFTER parser_agent (3.1) AND bank_statement_agent (3.4).

Estimates operational cash flow, evaluates debt servicing ability,
and produces a simple forward projection.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent3_analysis.config import DSCR_HEALTHY_MIN
from agent3_analysis.schemas.input_schema import Agent2Output
from agent3_analysis.schemas.output_schema import (
    AgentStatus,
    BankingBehaviourReport,
    Citation,
    CashFlowProjection,
    DataQuality,
    ParsedFinancials,
)
from agent3_analysis.utils.financial_utils import calc_dscr, safe_divide

logger = logging.getLogger(__name__)

# Projection horizon (years forward)
_PROJECTION_YEARS = 3
# Conservative growth rate for OCF projection when trend data is insufficient
_DEFAULT_GROWTH_RATE = 0.05


def run(
    input_data: Agent2Output,
    parsed_financials: ParsedFinancials,
    banking_behaviour: BankingBehaviourReport,
) -> CashFlowProjection:
    """
    Estimate operational cash flow and debt servicing ability.

    Args:
        input_data: Validated Agent 2 output.
        parsed_financials: Output of Agent 3.1.
        banking_behaviour: Output of Agent 3.4.

    Returns:
        CashFlowProjection with OCF, DSCR, projection, and debt servicing assessment.
    """
    logger.info("Agent 3.5 — Cash Flow Agent started.")

    overview = input_data.enriched_overview
    citations: List[Citation] = []

    # --- Derive base OCF ---
    # Primary: from cash_flow table (most recent year)
    base_ocf: Optional[float] = None
    base_year: Optional[str] = None

    cf_entries = sorted(
        [e for e in (input_data.cash_flow or []) if e.year],
        key=lambda x: str(x.year),
        reverse=True,
    )
    if cf_entries:
        latest_cf = cf_entries[0]
        base_ocf = latest_cf.cash_from_operating_activities
        base_year = latest_cf.year

    # Fallback: approximate OCF from EBITDA − finance_cost − tax (rough proxy)
    if base_ocf is None and overview:
        ebitda = overview.ebitda
        # Get most recent finance_cost from IS
        is_entries = sorted(
            [e for e in (input_data.income_statement or []) if e.year],
            key=lambda x: str(x.year),
            reverse=True,
        )
        finance_cost = is_entries[0].finance_cost if is_entries else None
        if ebitda is not None and finance_cost is not None:
            base_ocf = ebitda - finance_cost
            base_year = overview.metrics_year_income
            logger.debug("Agent 3.5 — Using EBITDA proxy for OCF: %s", base_ocf)

    # --- Debt service amount ---
    # Finance cost from IS + rough principal repayment estimate
    finance_cost_latest: Optional[float] = None
    is_entries = sorted(
        [e for e in (input_data.income_statement or []) if e.year],
        key=lambda x: str(x.year),
        reverse=True,
    )
    if is_entries:
        finance_cost_latest = is_entries[0].finance_cost
        if is_entries[0].source_documents:
            for doc in is_entries[0].source_documents:
                citations.append(Citation(
                    document=doc,
                    field="finance_cost",
                    year=is_entries[0].year,
                    source="document",
                ))

    # Use total_debt / 5 as rough annual principal repayment if no amortization schedule
    principal_repayment: Optional[float] = None
    if overview and overview.total_debt:
        principal_repayment = overview.total_debt / 5.0

    total_debt_service: Optional[float] = None
    if finance_cost_latest is not None and principal_repayment is not None:
        total_debt_service = finance_cost_latest + principal_repayment
    elif finance_cost_latest is not None:
        total_debt_service = finance_cost_latest

    # --- DSCR ---
    dscr = calc_dscr(base_ocf, total_debt_service)

    # --- Free cash flow ---
    # FCF = OCF − capex; capex not available, so FCF = OCF (conservative)
    free_cash_flow = base_ocf

    # --- Debt servicing ability classification ---
    debt_servicing_ability: Optional[str] = None
    if dscr is not None:
        if dscr >= DSCR_HEALTHY_MIN:
            debt_servicing_ability = "adequate"
        elif dscr >= 1.0:
            debt_servicing_ability = "stressed"
        else:
            debt_servicing_ability = "insufficient"
    elif base_ocf is not None and total_debt_service is None:
        debt_servicing_ability = "unable to assess — debt service data missing"

    # --- Incorporate bank behaviour if available ---
    bank_adjustment_note: Optional[str] = None
    if banking_behaviour and banking_behaviour.behaviour_score is not None:
        score = banking_behaviour.behaviour_score
        if score < 50:
            bank_adjustment_note = (
                f"Banking behaviour score ({score}/100) indicates irregular cash flows — "
                "OCF projection reliability is reduced."
            )
        elif score >= 80:
            bank_adjustment_note = (
                f"Banking behaviour score ({score}/100) supports cash flow projection reliability."
            )

    # --- Forward projection ---
    projection: List[Dict[str, Any]] = []
    if base_ocf is not None and base_year is not None:
        try:
            start_year = int(base_year)
            for i in range(1, _PROJECTION_YEARS + 1):
                proj_year = start_year + i
                projected_ocf = round(base_ocf * ((1 + _DEFAULT_GROWTH_RATE) ** i), 2)
                projection.append({"year": str(proj_year), "projected_ocf": projected_ocf})
        except (ValueError, TypeError):
            logger.warning("Agent 3.5 — Could not parse base_year '%s' for projection.", base_year)

    # --- Data quality ---
    if base_ocf is not None and dscr is not None:
        quality = DataQuality.COMPLETE
    elif base_ocf is not None:
        quality = DataQuality.PARTIAL
    else:
        quality = DataQuality.INSUFFICIENT

    if quality == DataQuality.INSUFFICIENT:
        logger.warning("Agent 3.5 — Insufficient data for cash flow analysis.")
        return CashFlowProjection(
            status=AgentStatus.PARTIAL,
            data_quality=DataQuality.INSUFFICIENT,
            narrative="Cash flow analysis could not be completed: OCF data unavailable.",
        )

    if overview:
        citations.append(Citation(
            document="enriched_overview",
            field="total_debt",
            year=overview.metrics_year_income,
            source="ZAUBA",
        ))

    narrative_parts = [f"Operational cash flow (FY{base_year}): {base_ocf:,.0f}."]
    if dscr is not None:
        narrative_parts.append(f"DSCR: {dscr:.2f} ({debt_servicing_ability}).")
    if bank_adjustment_note:
        narrative_parts.append(bank_adjustment_note)
    if projection:
        proj_str = ", ".join(
            "FY{}: {:,.0f}".format(p["year"], p["projected_ocf"]) for p in projection
        )
        narrative_parts.append(
            f"3-year OCF projection at {_DEFAULT_GROWTH_RATE:.0%} growth: {proj_str}."
        )
    narrative = " ".join(narrative_parts)

    logger.info(
        "Agent 3.5 — Completed. OCF=%s, DSCR=%s, Debt servicing=%s.",
        base_ocf, dscr, debt_servicing_ability,
    )

    return CashFlowProjection(
        status=AgentStatus.SUCCESS,
        data_quality=quality,
        operational_cash_flow=base_ocf,
        debt_service_coverage=dscr,
        free_cash_flow=free_cash_flow,
        projection=projection,
        debt_servicing_ability=debt_servicing_ability,
        narrative=narrative,
        citations=citations,
    )
