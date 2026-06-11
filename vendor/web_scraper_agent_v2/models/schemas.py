"""
models/schemas.py
All Pydantic models for the Web Scraper Agent in one place.

Sections:
  1. Enums          — MissingField, Source
  2. Input models   — mirrors transformation_output.json from Agent 1
  3. Field models   — RetrievedField, FlaggedField (scraper output wrappers)
  4. Output models  — EnrichOutput contract for Agent 3
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


# ══════════════════════════════════════════════════════════════════════════════
# 1. ENUMS
# ══════════════════════════════════════════════════════════════════════════════

class Source(str, Enum):
    MCA21       = "mca21"
    GST_PORTAL  = "gst_portal"
    ECOURTS     = "ecourts"
    ZAUBA       = "zauba"
    INCOME_TAX  = "income_tax"
    FLAG_MANUAL = "flag_manual"     # sentinel — never scraped


class MissingField(str, Enum):
    """Canonical names for every field the Gap Detector can flag."""
    # Overview
    CIN                = "cin"
    PAN                = "pan"
    GSTIN              = "gstin"
    ADDRESS            = "address"
    DIRECTORS          = "directors"
    INCORPORATION_DATE = "incorporation_date"
    INDUSTRY           = "industry"
    # Balance sheet
    EQUITY             = "equity"
    LONG_TERM_DEBT     = "long_term_debt"
    # Income statement
    GROSS_PROFIT       = "gross_profit"
    EBITDA             = "ebitda"
    EBIT               = "ebit"
    OPERATING_EXPENSES = "operating_expenses"
    DEPRECIATION       = "depreciation"
    INTEREST_EXPENSE   = "interest_expense"
    TAX                = "tax"
    # Tab-level
    CASH_FLOW_TAB      = "cash_flow_tab"
    CHARGES            = "charges"
    LEGAL_CASES        = "legal_cases"
    # Always-manual
    BANK_STATEMENTS      = "bank_statements"
    CIBIL_REPORT         = "cibil_report"
    PROPERTY_TITLE_DEEDS = "property_title_deeds"
    VALUATION_REPORT     = "valuation_report"
    LEGAL_OPINION_REPORT = "legal_opinion_report"
    ID_PROOF_DIRECTORS   = "id_proof_directors"


# ══════════════════════════════════════════════════════════════════════════════
# 2. INPUT MODELS  (mirrors transformation_output.json from Agent 1)
# ══════════════════════════════════════════════════════════════════════════════

class DocumentSummary(BaseModel):
    filename:        str
    doc_type:        str
    chars_extracted: int = 0
    tables_found:    int = 0


class RunSummary(BaseModel):
    run_timestamp:       Optional[str]        = None
    total_documents:     int                    = 0
    documents_processed: list[DocumentSummary] = Field(default_factory=list)
    tabs_populated:      dict[str, int]        = Field(default_factory=dict)
    error_count:         int                    = 0


class OverviewTab(BaseModel):
    company_name:       Optional[str]       = None
    industry:           Optional[str]       = None
    cin:                Optional[str]       = None
    pan:                Optional[str]       = None
    gstin:              Optional[str]       = None
    address:            Optional[str]       = None
    directors:          Optional[list[str]] = None
    incorporation_date: Optional[str]       = None


class BalanceSheetRow(BaseModel):
    year:                 Optional[str]   = None
    total_assets:         Optional[float] = None
    total_liabilities:    Optional[float] = None
    equity:               Optional[float] = None
    current_assets:       Optional[float] = None
    current_liabilities:  Optional[float] = None
    fixed_assets:         Optional[float] = None
    long_term_debt:       Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    source_document:      Optional[str]   = None


class IncomeStatementRow(BaseModel):
    year:               Optional[str]   = None
    revenue:            Optional[float] = None
    gross_profit:       Optional[float] = None
    ebitda:             Optional[float] = None
    ebit:               Optional[float] = None
    pat:                Optional[float] = None
    operating_expenses: Optional[float] = None
    depreciation:       Optional[float] = None
    interest_expense:   Optional[float] = None
    tax:                Optional[float] = None
    source_document:    Optional[str]   = None


class CashFlowRow(BaseModel):
    year:                Optional[str]   = None
    operating_cash_flow: Optional[float] = None
    investing_cash_flow: Optional[float] = None
    financing_cash_flow: Optional[float] = None
    net_cash_flow:       Optional[float] = None
    capital_expenditure: Optional[float] = None
    source_document:     Optional[str]   = None


class TabData(BaseModel):
    overview:         OverviewTab               = Field(default_factory=OverviewTab)
    balance_sheet:    list[BalanceSheetRow]     = Field(default_factory=list)
    income_statement: list[IncomeStatementRow]  = Field(default_factory=list)
    cash_flow:        list[CashFlowRow]         = Field(default_factory=list)


class TransformationOutput(BaseModel):
    """Root model for transformation_output.json produced by Agent 1."""
    status:              str
    summary:             RunSummary       = Field(default_factory=RunSummary)
    tab_data:            TabData          = Field(default_factory=TabData)
    auxiliary_data:      dict[str, Any]   = Field(default_factory=dict)
    structured_datasets: dict[str, Any]   = Field(default_factory=dict)
    errors:              list[str]        = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# 3. FIELD MODELS  (scraper output wrappers)
# ══════════════════════════════════════════════════════════════════════════════

class RetrievedField(BaseModel):
    """
    Every scraped value is wrapped in this — never store raw strings.
    Auto-flags itself if confidence drops below threshold.
    """
    field_name:      MissingField
    value:           Any
    source:          Source
    confidence:      float           = Field(ge=0.0, le=1.0)
    cross_validated: bool            = False
    retrieved_at:    datetime        = Field(
                         default_factory=lambda: datetime.now(timezone.utc)
                     )
    flagged:         bool            = False
    flag_reason:     Optional[str]   = None

    @model_validator(mode="after")
    def _auto_flag_low_confidence(self) -> "RetrievedField":
        from config.settings import CONFIDENCE_THRESHOLD
        if self.confidence < CONFIDENCE_THRESHOLD and not self.flagged:
            self.flagged     = True
            self.flag_reason = (
                f"Confidence {self.confidence:.2f} below "
                f"threshold {CONFIDENCE_THRESHOLD}"
            )
        return self


class FlaggedField(BaseModel):
    """A field that requires manual collection — produced by the Flag Engine."""
    field_name: MissingField
    reason:     str
    source:     Source   = Source.FLAG_MANUAL
    flagged_at: datetime = Field(
                    default_factory=lambda: datetime.now(timezone.utc)
                )


# ══════════════════════════════════════════════════════════════════════════════
# 4. OUTPUT MODELS  (contract for Agent 3)
# ══════════════════════════════════════════════════════════════════════════════

class EnrichmentSummary(BaseModel):
    run_timestamp:   datetime      = Field(
                         default_factory=lambda: datetime.now(timezone.utc)
                     )
    fields_detected: int           = 0
    fields_scraped:  int           = 0
    fields_flagged:  int           = 0
    fields_failed:   int           = 0
    sources_used:    list[str]     = Field(default_factory=list)
    errors:          list[str]     = Field(default_factory=list)


class EnrichOutput(BaseModel):
    """
    Root model for enrich_output.json consumed by Agent 3.

    enriched_tabs    — merged tab_data (original + scraped gaps filled)
    retrieved_fields — full audit trail of every scraped value
    flagged_manual   — fields requiring human action before analysis
    raw_scraped_data — unprocessed source payloads (for citation console)
    summary          — run statistics
    """
    status:           str
    enriched_tabs:    TabData              = Field(default_factory=TabData)
    retrieved_fields: list[RetrievedField] = Field(default_factory=list)
    flagged_manual:   list[FlaggedField]   = Field(default_factory=list)
    raw_scraped_data: dict[str, Any]       = Field(default_factory=dict)
    summary:          EnrichmentSummary    = Field(default_factory=EnrichmentSummary)
