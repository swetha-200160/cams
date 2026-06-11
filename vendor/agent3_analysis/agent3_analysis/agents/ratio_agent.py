"""
agents/ratio_agent.py
Agent 3.2 — Ratio Analysis Agent
Wave 2 (parallel): Reads parsed financials from state.

Uses enriched_overview top-level metrics (net_sales, ebitda, pat, networth, total_debt)
as primary inputs — these are more reliable than reconstructed values from sparse tables.
Falls back to income_statement rows where overview fields are null.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from agent3_analysis.config import (
    CURRENT_RATIO_HEALTHY_MIN,
    DEBT_EQUITY_HEALTHY_MAX,
    DSCR_HEALTHY_MIN,
    EBITDA_MARGIN_HEALTHY_MIN,
)
from agent3_analysis.schemas.input_schema import Agent2Output
from agent3_analysis.schemas.output_schema import (
    AgentStatus,
    Citation,
    DataQuality,
    RatioReport,
)
from agent3_analysis.utils.financial_utils import (
    calc_debt_to_equity,
    calc_dscr,
    calc_ebitda_margin,
    calc_gross_profit_margin,
    calc_interest_coverage,
    calc_net_profit_margin,
    safe_divide,
)

logger = logging.getLogger(__name__)


def run(input_data: Agent2Output) -> RatioReport:
    """
    Calculate key financial ratios from parsed financials.

    Args:
        input_data: Validated Agent 2 output.

    Returns:
        RatioReport with computed ratios, flags, and citations.
    """
    logger.info("Agent 3.2 — Ratio Analysis started.")

    overview = input_data.enriched_overview
    citations: List[Citation] = []
    flags: List[str] = []

    if not overview:
        logger.warning("Agent 3.2 — No enriched_overview. Skipping.")
        return RatioReport(
            status=AgentStatus.SKIPPED,
            data_quality=DataQuality.INSUFFICIENT,
            narrative="Insufficient data: enriched_overview not available.",
        )

    # --- Primary metric sources from overview ---
    net_sales = overview.net_sales
    ebitda = overview.ebitda
    pat = overview.pat
    networth = overview.networth
    total_debt = overview.total_debt
    metrics_year = overview.metrics_year_income or overview.metrics_year_balance

    if metrics_year:
        citations.append(Citation(
            document="enriched_overview",
            field="financial_metrics",
            year=metrics_year,
            source="ZAUBA",
        ))

    # --- Fallback: extract finance_cost and cost_of_material from income statement ---
    finance_cost: Optional[float] = None
    cost_of_material: Optional[float] = None

    if input_data.income_statement:
        # Use most recent year's income statement entry
        sorted_is = sorted(
            [e for e in input_data.income_statement if e.year],
            key=lambda x: str(x.year),
            reverse=True,
        )
        if sorted_is:
            latest = sorted_is[0]
            finance_cost = latest.finance_cost
            cost_of_material = latest.cost_of_material
            if latest.source_documents:
                for doc in latest.source_documents:
                    citations.append(Citation(
                        document=doc,
                        field="income_statement",
                        year=latest.year,
                        source="document",
                    ))

    # --- Ratio calculations ---
    ebitda_margin = calc_ebitda_margin(ebitda, net_sales)
    net_profit_margin = calc_net_profit_margin(pat, net_sales)
    debt_to_equity = calc_debt_to_equity(total_debt, networth)
    interest_coverage = calc_interest_coverage(ebitda, finance_cost)
    gross_profit_margin = calc_gross_profit_margin(net_sales, cost_of_material)

    # DSCR: use operating cash flow from cash_flow table if available
    ocf: Optional[float] = None
    if input_data.cash_flow:
        sorted_cf = sorted(
            [e for e in input_data.cash_flow if e.year],
            key=lambda x: str(x.year),
            reverse=True,
        )
        if sorted_cf:
            ocf = sorted_cf[0].cash_from_operating_activities

    dscr = calc_dscr(ocf, finance_cost) if (ocf and finance_cost) else None

    # Current ratio — read current_assets / current_liabilities from latest balance sheet row
    current_ratio: Optional[float] = None
    if input_data.balance_sheet:
        sorted_bs = sorted(
            [e for e in input_data.balance_sheet if e.year],
            key=lambda x: str(x.year),
            reverse=True,
        )
        for bs_entry in sorted_bs:
            if bs_entry.current_assets is not None and bs_entry.current_liabilities is not None and bs_entry.current_liabilities > 0:
                current_ratio = round(bs_entry.current_assets / bs_entry.current_liabilities, 4)
                break

    # --- Flag generation ---
    if ebitda_margin is not None and ebitda_margin < EBITDA_MARGIN_HEALTHY_MIN:
        flags.append(f"EBITDA margin ({ebitda_margin:.1%}) is below healthy threshold of {EBITDA_MARGIN_HEALTHY_MIN:.0%}.")
    if debt_to_equity is not None and debt_to_equity > DEBT_EQUITY_HEALTHY_MAX:
        flags.append(f"Debt-to-Equity ({debt_to_equity:.2f}x) exceeds healthy threshold of {DEBT_EQUITY_HEALTHY_MAX}x.")
    if dscr is not None and dscr < DSCR_HEALTHY_MIN:
        flags.append(f"DSCR ({dscr:.2f}) is below healthy minimum of {DSCR_HEALTHY_MIN}.")
    if networth is not None and networth < 0:
        flags.append(f"Negative networth ({networth:,.0f}) — company has eroded equity base.")
    if current_ratio is None:
        flags.append("Current ratio could not be computed — current assets/liabilities missing from balance sheet.")

    # --- Data quality ---
    computed = [x for x in [ebitda_margin, net_profit_margin, debt_to_equity, gross_profit_margin] if x is not None]
    if len(computed) >= 3:
        quality = DataQuality.COMPLETE
    elif len(computed) >= 1:
        quality = DataQuality.PARTIAL
    else:
        quality = DataQuality.INSUFFICIENT

    # --- Narrative summary ---
    narrative_parts = []
    if ebitda_margin is not None:
        narrative_parts.append(f"EBITDA margin stands at {ebitda_margin:.1%}.")
    if net_profit_margin is not None:
        narrative_parts.append(f"Net profit margin is {net_profit_margin:.1%}.")
    if debt_to_equity is not None:
        narrative_parts.append(f"Debt-to-equity ratio is {debt_to_equity:.2f}x.")
    if flags:
        narrative_parts.append(f"Flags raised: {'; '.join(flags)}")
    narrative = " ".join(narrative_parts) if narrative_parts else None

    logger.info(
        "Agent 3.2 — Ratios computed. EBITDA margin=%s, D/E=%s, DSCR=%s. Flags: %d.",
        ebitda_margin, debt_to_equity, dscr, len(flags),
    )

    return RatioReport(
        status=AgentStatus.SUCCESS if computed else AgentStatus.PARTIAL,
        data_quality=quality,
        dscr=dscr,
        current_ratio=current_ratio,
        debt_to_equity=debt_to_equity,
        ebitda_margin=ebitda_margin,
        gross_profit_margin=gross_profit_margin,
        net_profit_margin=net_profit_margin,
        interest_coverage_ratio=interest_coverage,
        flags=flags,
        narrative=narrative,
        citations=citations,
    )
