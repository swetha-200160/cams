# schemas/financial_schema.py
# ──────────────────────────────────────────────────────────────
# Pydantic models defining the CAMS financial data structure.
# Used by data_structuring_node (Node 6) to validate LLM output.
# All fields are Optional — missing data is null, never an error.
#
# CAMS-specified fields per tab:
#
#   Overview (summary figures pulled from other tabs):
#     net_sales, ebitda, pat, networth, total_debt
#
#   Balance Sheet:
#     share_capital, reserves_surplus, long_term_borrowing,
#     short_term_borrowing, trade_payables, fixed_assets
#
#   Income Statement:
#     revenue_from_operations, other_income, cost_of_material,
#     employee_benefit_expense, finance_cost, depreciation
#
#   Cash Flow:
#     operating_activities, investing_activities,
#     financing_activities, net_change_in_cash
# ──────────────────────────────────────────────────────────────

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Any


class BalanceSheet(BaseModel):
    """Single year balance sheet — CAMS required fields."""
    year:                  Optional[str]   = None

    @field_validator("year", mode="before")
    @classmethod
    def coerce_year_to_str(cls, v: Any) -> Optional[str]:
        """LLM sometimes returns year as int (2023) instead of str ('2023').
        Coerce silently so Pydantic validation never fails on this."""
        if v is None:
            return None
        return str(v)
    share_capital:         Optional[float] = None   # Equity share capital
    reserves_surplus:      Optional[float] = None   # Reserves & surplus / retained earnings
    long_term_borrowing:   Optional[float] = None   # Term loans, debentures, NCDs > 1 year
    short_term_borrowing:  Optional[float] = None   # Working capital loans, CC, OD < 1 year
    trade_payables:        Optional[float] = None   # Creditors / accounts payable
    fixed_assets:          Optional[float] = None   # Net block / PPE / tangible assets


class IncomeStatement(BaseModel):
    """Single year income statement / P&L — CAMS required fields."""
    year:                      Optional[str]   = None

    @field_validator("year", mode="before")
    @classmethod
    def coerce_year_to_str(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return str(v)
    revenue_from_operations:   Optional[float] = None   # Net sales / turnover from operations
    other_income:              Optional[float] = None   # Non-operating income / other income
    cost_of_material:          Optional[float] = None   # COGS / raw material consumed
    employee_benefit_expense:  Optional[float] = None   # Staff costs / salaries / wages
    finance_cost:              Optional[float] = None   # Interest expense / borrowing costs
    depreciation:              Optional[float] = None   # Depreciation & amortisation (D&A)


class CashFlow(BaseModel):
    """Single year cash flow statement — CAMS required fields."""
    year:                  Optional[str]   = None

    @field_validator("year", mode="before")
    @classmethod
    def coerce_year_to_str(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return str(v)
    operating_activities:  Optional[float] = None   # Cash from / used in operating activities
    investing_activities:  Optional[float] = None   # Cash from / used in investing activities
    financing_activities:  Optional[float] = None   # Cash from / used in financing activities
    net_change_in_cash:    Optional[float] = None   # Net increase / decrease in cash & equivalents


class RatioAnalysisData(BaseModel):
    """
    Single year data for ratio analysis — consumed by the Ratio Analysis Agent.
    Sits alongside cash flow data in the pipeline output.
    Fields cover P&L derivations, debt-service metrics, balance sheet
    sub-items, and tax/EBITDA figures that standard tabs do not expose.
    """
    year: Optional[str] = None

    @field_validator("year", mode="before")
    @classmethod
    def coerce_year_to_str(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return str(v)

    # ── Revenue & Operating metrics ───────────────────────────
    total_revenue_from_operations: Optional[float] = None   # Net sales / revenue from operations
    total_revenue:                 Optional[float] = None   # Total revenue incl. other income
    net_operating_income:          Optional[float] = None   # Revenue minus operating expenses
    operating_expense:             Optional[float] = None   # Total operating expenses
    other_operating_expense:       Optional[float] = None   # Other operating expenses (excl. COGS/employee)
    employee_benefit_expense:      Optional[float] = None   # Staff costs / salaries / wages
    ebitda:                        Optional[float] = None   # Earnings before interest, tax, D&A

    # ── Profitability ─────────────────────────────────────────
    profit_before_tax:  Optional[float] = None   # PBT / EBIT
    current_tax:        Optional[float] = None   # Current year income tax provision
    deferred_tax:       Optional[float] = None   # Deferred tax charge / (credit)
    net_income:         Optional[float] = None   # PAT / profit after tax / net profit

    # ── Depreciation ──────────────────────────────────────────
    depreciation_amortization: Optional[float] = None   # D&A / depreciation & amortisation

    # ── Debt Service metrics ──────────────────────────────────
    finance_cost:            Optional[float] = None   # Total finance cost / borrowing costs
    interest_payment:        Optional[float] = None   # Interest paid / interest expense
    principal_repayment:     Optional[float] = None   # Loan principal repaid during the year
    total_debt_service:      Optional[float] = None   # Interest + Principal repayment (DSCR denominator)
    fixed_payment_obligation:Optional[float] = None   # Lease / fixed charges / fixed obligations

    # ── Debt position ─────────────────────────────────────────
    opening_debt: Optional[float] = None   # Total borrowings at start of year
    closing_debt: Optional[float] = None   # Total borrowings at end of year

    # ── Balance sheet sub-items ───────────────────────────────
    current_assets:      Optional[float] = None   # Current assets total
    current_liabilities: Optional[float] = None   # Current liabilities total
    shareholder_equity:  Optional[float] = None   # Total equity (share capital + reserves)
    share_capital:       Optional[float] = None   # Equity share capital
    reserves_surplus:    Optional[float] = None   # Reserves & surplus / retained earnings


class BankTransaction(BaseModel):
    """Single bank statement transaction row."""
    date:        Optional[str]   = None   # YYYY-MM-DD
    amount:      Optional[float] = None   # Positive transaction amount
    type:        Optional[str]   = None   # "credit" | "debit"
    balance:     Optional[float] = None   # Running balance after transaction
    description: Optional[str]   = None   # Narration / transaction description
    mode:        Optional[str]   = None   # "cash" | "cheque" | "neft" | "rtgs" | "upi" | "imps" | "online" | "other"
    category:    Optional[str]   = None   # "business_income" | "salary" | "loan_repayment" | "vendor_payment" | "tax_payment" | "cash_withdrawal" | "bank_charges" | "interest" | "other"
    is_cash:     Optional[bool]  = None
    is_cheque:   Optional[bool]  = None
    is_bounce:   Optional[bool]  = None


class ITRData(BaseModel):
    """
    ITR data extracted from a single Income Tax Return document.
    Flat structure — tab_mapping consolidates across years and computes flags.
    """
    period:                 Optional[str]   = None   # e.g. "FY2025-26", "AY2025-26"
    itr_form:               Optional[str]   = None   # "ITR-6", "ITR-3", etc.

    # ── Filing status ─────────────────────────────────────────
    section:                Optional[str]   = None   # "139(1)" | "139(4)" | "139(5)"
    filed_on_time:          Optional[bool]  = None
    audit_applicable:       Optional[bool]  = None
    audit_completed:        Optional[bool]  = None

    # ── Income ────────────────────────────────────────────────
    reported_total_income:  Optional[float] = None
    taxable_income:         Optional[float] = None

    # ── TDS ───────────────────────────────────────────────────
    total_receipts:         Optional[float] = None   # Gross receipts on which TDS deducted
    tds_deducted:           Optional[float] = None

    # ── Tax ───────────────────────────────────────────────────
    total_liability:        Optional[float] = None
    tds_credit:             Optional[float] = None
    advance_tax:            Optional[float] = None
    net_payable:            Optional[float] = None


class GSTMonthlyValue(BaseModel):
    """One month's taxable value from a GST return."""
    month: Optional[str]   = None   # "Apr", "May", ..., "Mar"
    value: Optional[float] = None   # Taxable value for the month


class GSTData(BaseModel):
    """
    GST data extracted from a single GST return document (GSTR-1, GSTR-3B, GSTR-9, etc.).
    Flat structure — tab_mapping merges across docs and computes derived fields.
    """
    period:               Optional[str]              = None   # e.g. "FY25-26", "FY2025-26"
    gst_source:           Optional[str]              = None   # doc type: "GSTR-1", "GSTR-3B", etc.

    # ── Sales figures ─────────────────────────────────────────
    monthly_taxable_value: Optional[List[GSTMonthlyValue]] = Field(default_factory=list)
    annual_taxable_value:  Optional[float]           = None

    # ── Tax paid (from GSTR-3B) ───────────────────────────────
    igst:           Optional[float] = None
    cgst:           Optional[float] = None
    sgst:           Optional[float] = None
    total_tax_paid: Optional[float] = None

    # ── Consistency fields ────────────────────────────────────
    gstr1_total_sales:  Optional[float] = None   # From GSTR-1
    gstr3b_total_sales: Optional[float] = None   # From GSTR-3B

    # ── Sales breakdown (from GSTR-1) ─────────────────────────
    b2b_sales:      Optional[float] = None
    export_sales:   Optional[float] = None
    domestic_sales: Optional[float] = None


class FinancialSchema(BaseModel):
    """
    Top-level schema for a single borrower document.
    Populated by data_structuring_node and validated via Pydantic.
    Passed to tab_mapping_node which routes into the 4 UI tabs.
    """
    # ── Company Identifiers ───────────────────────────────────
    company_name: Optional[str] = None
    industry:     Optional[str] = None
    cin:          Optional[str] = None   # Corporate Identification Number
    pan:          Optional[str] = None   # Permanent Account Number

    # ── Financial Data — multi-year lists ─────────────────────
    balance_sheets:       Optional[List[BalanceSheet]]       = Field(default_factory=list)
    income_statements:    Optional[List[IncomeStatement]]    = Field(default_factory=list)
    cash_flows:           Optional[List[CashFlow]]           = Field(default_factory=list)
    ratio_analysis_data:  Optional[List[RatioAnalysisData]]  = Field(default_factory=list)

    # ── Bank Statement Transactions ───────────────────────────
    bank_transactions:    Optional[List[BankTransaction]]    = Field(default_factory=list)

    # ── GST Data ─────────────────────────────────────────────
    gst_data:             Optional["GSTData"]                = None

    # ── ITR Data ─────────────────────────────────────────────
    itr_data:             Optional[ITRData]                  = None

    # ── Raw notes ─────────────────────────────────────────────
    raw_notes: Optional[str] = None