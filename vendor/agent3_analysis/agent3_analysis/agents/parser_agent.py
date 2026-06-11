"""
agents/parser_agent.py
Agent 3.1 — Financial Statement Parser Agent
Wave 1 (foundation): Runs first. All downstream agents consume its output from state.

Responsibilities:
- Normalize Agent 2's sparse financial tables into a consistent structure
- Tag every value with its source document for citation traceability
- Emit null_field_warnings so downstream agents know what's missing
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent3_analysis.schemas.input_schema import Agent2Output
from agent3_analysis.schemas.output_schema import (
    AgentStatus,
    Citation,
    DataQuality,
    ParsedFinancials,
)
from agent3_analysis.utils.financial_utils import count_nulls

logger = logging.getLogger(__name__)

# Fields expected in a complete balance sheet entry
_BS_FIELDS = [
    "share_capital", "reserves_surplus", "long_term_borrowing",
    "short_term_borrowing", "trade_payables", "fixed_assets",
]

# Fields expected in a complete income statement entry
_IS_FIELDS = [
    "revenue_from_operations", "other_income", "cost_of_material",
    "employee_benefit_expense", "finance_cost", "depreciation",
]

# Fields expected in a complete cash flow entry
_CF_FIELDS = [
    "cash_from_operating_activities", "cash_from_investing_activities",
    "cash_from_financing_activities", "net_change_in_cash",
]


def run(input_data: Agent2Output) -> ParsedFinancials:
    """
    Parse and normalize financial statements from Agent 2 output.

    Args:
        input_data: Validated Agent 2 output.

    Returns:
        ParsedFinancials populated with normalized tables, warnings, and citations.
    """
    logger.info("Agent 3.1 — Financial Statement Parser started.")

    citations: List[Citation] = []
    null_warnings: List[str] = []

    # --- Balance Sheet ---
    balance_sheet: List[Dict[str, Any]] = []
    for entry in (input_data.balance_sheet or []):
        row = entry.model_dump()
        nulls = count_nulls(row, _BS_FIELDS)
        if nulls:
            null_warnings.append(
                f"Balance Sheet {entry.year}: null fields → {', '.join(nulls)}"
            )
        balance_sheet.append(row)
        for doc in (entry.source_documents or []):
            citations.append(Citation(
                document=doc,
                field="balance_sheet",
                year=entry.year,
                source="document",
            ))

    # --- Income Statement ---
    income_statement: List[Dict[str, Any]] = []
    for entry in (input_data.income_statement or []):
        row = entry.model_dump()
        nulls = count_nulls(row, _IS_FIELDS)
        if nulls:
            null_warnings.append(
                f"Income Statement {entry.year}: null fields → {', '.join(nulls)}"
            )
        income_statement.append(row)
        for doc in (entry.source_documents or []):
            citations.append(Citation(
                document=doc,
                field="income_statement",
                year=entry.year,
                source="document",
            ))

    # --- Cash Flow ---
    cash_flow: List[Dict[str, Any]] = []
    for entry in (input_data.cash_flow or []):
        row = entry.model_dump()
        nulls = count_nulls(row, _CF_FIELDS)
        if nulls:
            null_warnings.append(
                f"Cash Flow {entry.year}: null fields → {', '.join(nulls)}"
            )
        cash_flow.append(row)

    # --- Years available ---
    years_is = {e.year for e in (input_data.income_statement or []) if e.year}
    years_bs = {e.year for e in (input_data.balance_sheet or []) if e.year}
    years_cf = {e.year for e in (input_data.cash_flow or []) if e.year}
    years_available = sorted(years_is | years_bs | years_cf)

    # --- Enrich with enriched_overview metrics as fallback ---
    overview = input_data.enriched_overview
    if overview:
        citations.append(Citation(
            document="enriched_overview",
            field="company_profile",
            year=overview.metrics_year_income,
            source="ZAUBA",
        ))

    # --- Data quality assessment ---
    total_nulls = len(null_warnings)
    if total_nulls == 0:
        quality = DataQuality.COMPLETE
    elif total_nulls <= 5:
        quality = DataQuality.PARTIAL
    else:
        quality = DataQuality.INSUFFICIENT

    if not balance_sheet and not income_statement:
        logger.warning("Agent 3.1 — No financial statement data available.")
        return ParsedFinancials(
            status=AgentStatus.SKIPPED,
            data_quality=DataQuality.INSUFFICIENT,
            company_name=overview.company_name if overview else None,
            years_available=[],
            null_field_warnings=["No balance sheet or income statement data found."],
            citations=citations,
        )

    logger.info(
        "Agent 3.1 — Parsed %d BS, %d IS, %d CF entries. Years: %s. Null warnings: %d.",
        len(balance_sheet), len(income_statement), len(cash_flow),
        years_available, total_nulls,
    )

    return ParsedFinancials(
        status=AgentStatus.SUCCESS,
        data_quality=quality,
        company_name=overview.company_name if overview else None,
        years_available=years_available,
        balance_sheet=balance_sheet,
        income_statement=income_statement,
        cash_flow=cash_flow,
        null_field_warnings=null_warnings,
        citations=citations,
    )
