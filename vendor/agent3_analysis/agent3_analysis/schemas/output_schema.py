"""
schemas/output_schema.py
Pydantic output schema for Agent 3 insights payload.
This is consumed by:
  - The Insights tab (frontend display)
  - Agent 4 (CAM Draft Generator) for citation-backed report generation

Every sub-agent section includes a `citations` list for BRD-required traceability.
Every section has a `data_quality` flag so the frontend can show warnings on sparse data.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

class DataQuality(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"   # Agent ran but data was too sparse to compute


class Citation(BaseModel):
    document: str                   # e.g. "Audited Financial Statements – Last 3 Years.pdf"
    field: Optional[str] = None     # e.g. "revenue_from_operations"
    year: Optional[str] = None      # e.g. "2024"
    source: Optional[str] = None    # e.g. "ZAUBA", "HARDCODED"


class AgentStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"             # Ran but some sub-computations skipped due to missing data
    SKIPPED = "skipped"             # Insufficient data to run at all
    ERROR = "error"


# ---------------------------------------------------------------------------
# Per sub-agent output sections
# ---------------------------------------------------------------------------

class ParsedFinancials(BaseModel):
    """3.1 — Financial Statement Parser"""
    status: AgentStatus = AgentStatus.SUCCESS
    data_quality: DataQuality = DataQuality.COMPLETE
    company_name: Optional[str] = None
    years_available: List[str] = Field(default_factory=list)
    balance_sheet: List[Dict[str, Any]] = Field(default_factory=list)
    income_statement: List[Dict[str, Any]] = Field(default_factory=list)
    cash_flow: List[Dict[str, Any]] = Field(default_factory=list)
    null_field_warnings: List[str] = Field(default_factory=list)   # fields that were null
    citations: List[Citation] = Field(default_factory=list)


class RatioReport(BaseModel):
    """3.2 — Ratio Analysis"""
    status: AgentStatus = AgentStatus.SUCCESS
    data_quality: DataQuality = DataQuality.COMPLETE
    dscr: Optional[float] = None
    current_ratio: Optional[float] = None
    debt_to_equity: Optional[float] = None
    ebitda_margin: Optional[float] = None
    gross_profit_margin: Optional[float] = None
    net_profit_margin: Optional[float] = None
    interest_coverage_ratio: Optional[float] = None
    flags: List[str] = Field(default_factory=list)      # e.g. ["DSCR below healthy threshold"]
    narrative: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)


class TrendReport(BaseModel):
    """3.3 — Trend Analysis"""
    status: AgentStatus = AgentStatus.SUCCESS
    data_quality: DataQuality = DataQuality.COMPLETE
    revenue_trend: List[Dict[str, Any]] = Field(default_factory=list)   # [{year, value, yoy_growth}]
    ebitda_trend: List[Dict[str, Any]] = Field(default_factory=list)
    pat_trend: List[Dict[str, Any]] = Field(default_factory=list)
    anomalies: List[str] = Field(default_factory=list)
    improving_metrics: List[str] = Field(default_factory=list)
    declining_metrics: List[str] = Field(default_factory=list)
    narrative: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)


class BankingBehaviourReport(BaseModel):
    """3.4 — Bank Statement Analyzer"""
    status: AgentStatus = AgentStatus.SUCCESS
    data_quality: DataQuality = DataQuality.COMPLETE
    behaviour_score: Optional[float] = None     # 0–100
    avg_monthly_inflow: Optional[float] = None
    avg_monthly_outflow: Optional[float] = None
    cheque_bounce_count: Optional[int] = None
    large_cash_deposit_count: Optional[int] = None
    unusual_pattern_flags: List[str] = Field(default_factory=list)
    narrative: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)


class CashFlowProjection(BaseModel):
    """3.5 — Cash Flow Agent"""
    status: AgentStatus = AgentStatus.SUCCESS
    data_quality: DataQuality = DataQuality.COMPLETE
    operational_cash_flow: Optional[float] = None
    debt_service_coverage: Optional[float] = None
    free_cash_flow: Optional[float] = None
    projection: List[Dict[str, Any]] = Field(default_factory=list)  # [{year, projected_ocf}]
    debt_servicing_ability: Optional[str] = None    # "adequate" | "stressed" | "insufficient"
    narrative: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)


class GSTAnalyticsReport(BaseModel):
    """3.6 — GST Analytics Agent"""
    status: AgentStatus = AgentStatus.SUCCESS
    data_quality: DataQuality = DataQuality.COMPLETE
    revenue_authenticity_score: Optional[float] = None  # 0–100
    gst_reported_sales: Optional[float] = None
    financial_reported_sales: Optional[float] = None
    discrepancy_pct: Optional[float] = None
    discrepancy_flag: bool = False
    narrative: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)


class TaxComplianceReport(BaseModel):
    """3.7 — Tax Compliance Agent"""
    status: AgentStatus = AgentStatus.SUCCESS
    data_quality: DataQuality = DataQuality.COMPLETE
    compliance_status: Optional[str] = None     # "compliant" | "non-compliant" | "partial"
    filings_verified: List[str] = Field(default_factory=list)
    income_cross_check_flag: bool = False
    flags: List[str] = Field(default_factory=list)
    narrative: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)


class RelatedPartyReport(BaseModel):
    """3.8 — Related Party Detection Agent"""
    status: AgentStatus = AgentStatus.SUCCESS
    data_quality: DataQuality = DataQuality.COMPLETE
    risk_alerts: List[str] = Field(default_factory=list)
    related_party_transactions_detected: bool = False
    open_charges: List[Dict[str, Any]] = Field(default_factory=list)
    director_risk_flags: List[str] = Field(default_factory=list)
    narrative: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)


class IndustryIntelligenceReport(BaseModel):
    """3.9 — Industry Intelligence Agent"""
    status: AgentStatus = AgentStatus.SUCCESS
    data_quality: DataQuality = DataQuality.COMPLETE
    industry_classification: Optional[str] = None
    growth_rate_estimate: Optional[str] = None
    industry_risks: List[str] = Field(default_factory=list)
    competitive_landscape: Optional[str] = None
    narrative: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)


class MarketRiskReport(BaseModel):
    """3.10 — Market Risk Agent"""
    status: AgentStatus = AgentStatus.SUCCESS
    data_quality: DataQuality = DataQuality.COMPLETE
    macro_risks: List[str] = Field(default_factory=list)
    sector_volatility: Optional[str] = None    # "low" | "moderate" | "high"
    interest_rate_sensitivity: Optional[str] = None
    regulatory_risk: Optional[str] = None
    narrative: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Root insights output — merged payload from all 10 sub-agents
# ---------------------------------------------------------------------------

class InsightsOutput(BaseModel):
    """
    Root output of Agent 3.
    Returned by POST /analyze and stored for Insights tab + CAM generation.
    """
    status: AgentStatus = AgentStatus.SUCCESS
    run_timestamp: Optional[str] = None
    company_name: Optional[str] = None
    cin: Optional[str] = None

    # Sub-agent sections
    parsed_financials: Optional[ParsedFinancials] = None
    ratio_report: Optional[RatioReport] = None
    trend_report: Optional[TrendReport] = None
    banking_behaviour: Optional[BankingBehaviourReport] = None
    cash_flow_projection: Optional[CashFlowProjection] = None
    gst_analytics: Optional[GSTAnalyticsReport] = None
    tax_compliance: Optional[TaxComplianceReport] = None
    related_party: Optional[RelatedPartyReport] = None
    industry_intelligence: Optional[IndustryIntelligenceReport] = None
    market_risk: Optional[MarketRiskReport] = None

    # Top-level execution summary
    agents_executed: List[str] = Field(default_factory=list)
    agents_skipped: List[str] = Field(default_factory=list)
    agents_failed: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
