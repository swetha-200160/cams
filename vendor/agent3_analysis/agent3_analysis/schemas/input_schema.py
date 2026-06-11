"""
schemas/input_schema.py
Pydantic input schema that mirrors Agent 2 (Web Scraper Agent) output.
All fields are Optional to handle sparse / partial data gracefully.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class ChargeRecord(BaseModel):
    charge_id: Optional[str] = None
    creation_date: Optional[str] = None
    closure_date: Optional[str] = None          # Empty string means still open
    amount: Optional[str] = None                # String with commas e.g. "1,175,000,000.00"
    holder: Optional[str] = None


class RetrievedField(BaseModel):
    field_name: str
    value: Any
    source: Optional[str] = None
    confidence: Optional[float] = None
    notes: Optional[str] = None


class EnrichedOverview(BaseModel):
    cin: Optional[str] = None
    pan: Optional[str] = None
    company_name: Optional[str] = None
    net_sales: Optional[float] = None
    ebitda: Optional[float] = None
    pat: Optional[float] = None
    networth: Optional[float] = None
    total_debt: Optional[float] = None
    metrics_year_income: Optional[str] = None
    metrics_year_balance: Optional[str] = None
    gstin: Optional[str] = None
    incorporation_date: Optional[str] = None
    registered_address: Optional[str] = None
    industry: Optional[str] = None
    directors: Optional[List[str]] = Field(default_factory=list)
    charges: Optional[List[ChargeRecord]] = Field(default_factory=list)
    legal_cases: Optional[List[Any]] = Field(default_factory=list)


class BalanceSheetEntry(BaseModel):
    year: Optional[str] = None
    share_capital: Optional[float] = None
    reserves_surplus: Optional[float] = None
    long_term_borrowing: Optional[float] = None
    short_term_borrowing: Optional[float] = None
    trade_payables: Optional[float] = None
    fixed_assets: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    networth: Optional[float] = None
    source_document: Optional[str] = None
    source_documents: Optional[List[str]] = Field(default_factory=list)


class IncomeStatementEntry(BaseModel):
    year: Optional[str] = None
    revenue_from_operations: Optional[float] = None
    other_income: Optional[float] = None
    cost_of_material: Optional[float] = None
    employee_benefit_expense: Optional[float] = None
    finance_cost: Optional[float] = None
    depreciation: Optional[float] = None
    source_document: Optional[str] = None
    source_documents: Optional[List[str]] = Field(default_factory=list)


class CashFlowEntry(BaseModel):
    year: Optional[str] = None
    cash_from_operating_activities: Optional[float] = None
    cash_from_investing_activities: Optional[float] = None
    cash_from_financing_activities: Optional[float] = None
    net_change_in_cash: Optional[float] = None


class RunSummary(BaseModel):
    run_timestamp: Optional[str] = None
    fields_retrieved: Optional[int] = None
    fields_flagged: Optional[int] = None
    sources_used: Optional[List[str]] = Field(default_factory=list)
    errors: Optional[List[str]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level Agent 2 output
# ---------------------------------------------------------------------------

class Agent2Output(BaseModel):
    """
    Root model for Agent 2 (Web Scraper Agent) output.
    Passed directly as input to Agent 3.
    """
    status: Optional[str] = None
    summary: Optional[RunSummary] = None
    enriched_overview: Optional[EnrichedOverview] = None
    retrieved_fields: Optional[List[RetrievedField]] = Field(default_factory=list)
    flagged_fields: Optional[List[Any]] = Field(default_factory=list)
    balance_sheet: Optional[List[BalanceSheetEntry]] = Field(default_factory=list)
    income_statement: Optional[List[IncomeStatementEntry]] = Field(default_factory=list)
    cash_flow: Optional[List[CashFlowEntry]] = Field(default_factory=list)

    # Future fields from Agent 2 expansions — absorbed without breaking
    bank_statements: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    gst_returns: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    itr_filings: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    roc_filings: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
