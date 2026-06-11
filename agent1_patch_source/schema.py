from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


class ChargeRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    charge_id: Optional[str] = None
    holder: Optional[str] = None
    amount: Optional[float] = None
    created_on: Optional[str] = None
    modified_on: Optional[str] = None
    creation_date: Optional[str] = None
    closure_date: Optional[str] = None
    status: Optional[str] = None

    @model_validator(mode="after")
    def sync_normalized_dates(self) -> "ChargeRecord":
        if not self.creation_date and self.created_on:
            self.creation_date = self.created_on
        if not self.created_on and self.creation_date:
            self.created_on = self.creation_date
        if not self.closure_date and self.modified_on and str(self.status or "").lower() in {"closed", "satisfied", "released"}:
            self.closure_date = self.modified_on
        return self


class EnrichedOverview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cin: Optional[str] = None
    pan: Optional[str] = None
    company_name: Optional[str] = None
    gstin: Optional[str] = None
    incorporation_date: Optional[str] = None
    registered_address: Optional[str] = None
    industry: Optional[str] = None
    directors: Optional[List[str]] = Field(default_factory=list)
    charges: Optional[List[ChargeRecord]] = Field(default_factory=list)
    legal_cases: Optional[List[Any]] = Field(default_factory=list)

    net_sales: Optional[float] = None
    ebitda: Optional[float] = None
    pat: Optional[float] = None
    tax_expense: Optional[float] = None
    pbt: Optional[float] = None
    networth: Optional[float] = None
    total_debt: Optional[float] = None
    metrics_year_income: Optional[str] = None
    metrics_year_balance: Optional[str] = None


class BalanceSheetEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    year: str = Field(
        description=(
            "Financial year in YYYY format (e.g., 2023). "
            "If input is FY23 or 2022-23, normalize to 2023."
        )
    )

    @field_validator("year", mode="before")
    @classmethod
    def coerce_year(cls, v: Any) -> str:
        return str(v) if v is not None else ""

    share_capital: Optional[float] = None
    reserves_surplus: Optional[float] = None
    networth: Optional[float] = None
    long_term_borrowing: Optional[float] = None
    short_term_borrowing: Optional[float] = None
    total_debt: Optional[float] = None
    trade_payables: Optional[float] = None
    fixed_assets: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    inventory: Optional[float] = None
    receivables: Optional[float] = None
    cash_and_bank: Optional[float] = None
    total_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    source_document: Optional[str] = None
    source_documents: Optional[List[str]] = Field(default_factory=list)


class IncomeStatementEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    year: str = Field(
        description=(
            "Financial year in YYYY format (e.g., 2023). "
            "If input is FY23 or 2022-23, normalize to 2023."
        )
    )

    @field_validator("year", mode="before")
    @classmethod
    def coerce_year(cls, v: Any) -> str:
        return str(v) if v is not None else ""

    revenue_from_operations: Optional[float] = None
    other_income: Optional[float] = None
    cost_of_material: Optional[float] = None
    employee_benefit_expense: Optional[float] = None
    finance_cost: Optional[float] = None
    depreciation: Optional[float] = None
    total_expenses: Optional[float] = None
    ebitda: Optional[float] = None
    pbt: Optional[float] = None
    tax_expense: Optional[float] = None
    pat: Optional[float] = None
    source_document: Optional[str] = None
    source_documents: Optional[List[str]] = Field(default_factory=list)


class CashFlowEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    year: str = Field(
        description=(
            "Financial year in YYYY format (e.g., 2023). "
            "If input is FY23 or 2022-23, normalize to 2023."
        )
    )

    @field_validator("year", mode="before")
    @classmethod
    def coerce_year(cls, v: Any) -> str:
        return str(v) if v is not None else ""

    operating_activities: Optional[float] = None
    investing_activities: Optional[float] = None
    financing_activities: Optional[float] = None
    net_change_in_cash: Optional[float] = None
    source_document: Optional[str] = None
    source_documents: Optional[List[str]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_cashflow_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        alias_map = {
            "cash_from_operating_activities": "operating_activities",
            "cash_from_investing_activities": "investing_activities",
            "cash_from_financing_activities": "financing_activities",
        }
        normalized = dict(data)
        for alias, canonical in alias_map.items():
            if normalized.get(canonical) is None and normalized.get(alias) is not None:
                normalized[canonical] = normalized[alias]
        return normalized


class BalanceSheetData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entries: List[BalanceSheetEntry] = Field(
        default_factory=list,
        description=(
            "MANDATORY: Extract ALL financial years present in the document. "
            "Each entry MUST represent exactly ONE year. "
            "Do NOT merge multiple years into one entry. "
            "If 3 years exist, return exactly 3 entries."
        ),
    )


class IncomeStatementData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entries: List[IncomeStatementEntry] = Field(
        default_factory=list,
        description=(
            "MANDATORY: Extract ALL financial years present in the document. "
            "Each entry MUST represent exactly ONE year. "
            "Do NOT merge multiple years into one entry. "
            "If 3 years exist, return exactly 3 entries."
        ),
    )


class CashFlowData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entries: List[CashFlowEntry] = Field(
        default_factory=list,
        description=(
            "MANDATORY: Extract ALL financial years present in the document. "
            "Each entry MUST represent exactly ONE year. "
            "Do NOT merge multiple years into one entry. "
            "If 3 years exist, return exactly 3 entries."
        ),
    )
