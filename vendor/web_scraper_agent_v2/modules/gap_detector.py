"""
modules/gap_detector.py
LangGraph node: gap_detector
"""

from __future__ import annotations

import logging
from typing import Any

from core.state import GapDetectionError
from core.state import AgentState
from models.schemas import TransformationOutput
from models.schemas import MissingField

logger = logging.getLogger(__name__)

_FIELD_PRIORITY: dict[MissingField, int] = {
    MissingField.CIN:               1,
    MissingField.PAN:               1,
    MissingField.GSTIN:             1,
    MissingField.INCORPORATION_DATE:2,
    MissingField.DIRECTORS:         2,
    MissingField.EQUITY:            2,
    MissingField.LONG_TERM_DEBT:    2,
    MissingField.GROSS_PROFIT:      2,
    MissingField.EBITDA:            2,
    MissingField.EBIT:              2,
    MissingField.OPERATING_EXPENSES:2,
    MissingField.DEPRECIATION:      2,
    MissingField.INTEREST_EXPENSE:  2,
    MissingField.TAX:               2,
    MissingField.CASH_FLOW_TAB:     2,
    MissingField.CHARGES:           2,
    MissingField.LEGAL_CASES:       3,
    MissingField.ADDRESS:           3,
    MissingField.INDUSTRY:          3,
    MissingField.BANK_STATEMENTS:       3,
    MissingField.CIBIL_REPORT:          3,
    MissingField.PROPERTY_TITLE_DEEDS:  3,
    MissingField.VALUATION_REPORT:      3,
    MissingField.LEGAL_OPINION_REPORT:  3,
    MissingField.ID_PROOF_DIRECTORS:    3,
}


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and not value:
        return True
    return False


def _has_aux(tx: TransformationOutput, key: str) -> bool:
    value = (tx.auxiliary_data or {}).get(key)
    return bool(value)


def _detect_overview_gaps(overview: Any) -> list[MissingField]:
    gaps: list[MissingField] = []
    field_map = {
        "cin":               MissingField.CIN,
        "pan":               MissingField.PAN,
        "gstin":             MissingField.GSTIN,
        "address":           MissingField.ADDRESS,
        "directors":         MissingField.DIRECTORS,
        "incorporation_date":MissingField.INCORPORATION_DATE,
        "industry":          MissingField.INDUSTRY,
    }
    for attr, missing_field in field_map.items():
        if _is_null(getattr(overview, attr, None)):
            gaps.append(missing_field)
    return gaps


def _detect_balance_sheet_gaps(rows: list[Any]) -> list[MissingField]:
    gaps: set[MissingField] = set()
    nullable_fields = {
        "equity":         MissingField.EQUITY,
        "long_term_debt": MissingField.LONG_TERM_DEBT,
    }
    for row in rows:
        for attr, missing_field in nullable_fields.items():
            if _is_null(getattr(row, attr, None)):
                gaps.add(missing_field)
    return list(gaps)


def _detect_income_statement_gaps(rows: list[Any]) -> list[MissingField]:
    gaps: set[MissingField] = set()
    nullable_fields = {
        "gross_profit":       MissingField.GROSS_PROFIT,
        "ebitda":             MissingField.EBITDA,
        "ebit":               MissingField.EBIT,
        "operating_expenses": MissingField.OPERATING_EXPENSES,
        "depreciation":       MissingField.DEPRECIATION,
        "interest_expense":   MissingField.INTEREST_EXPENSE,
        "tax":                MissingField.TAX,
    }
    for row in rows:
        for attr, missing_field in nullable_fields.items():
            if _is_null(getattr(row, attr, None)):
                gaps.add(missing_field)
    return list(gaps)


def gap_detector(state: AgentState) -> dict:
    logger.info("=== Gap Detector: starting ===")
    errors: list[str] = list(state.get("errors", []))
    try:
        tx: TransformationOutput = state["transformation_input"]
    except KeyError as exc:
        raise GapDetectionError("transformation_input missing from state") from exc

    missing: list[MissingField] = []
    try:
        missing += _detect_overview_gaps(tx.tab_data.overview)
        if tx.tab_data.balance_sheet:
            missing += _detect_balance_sheet_gaps(tx.tab_data.balance_sheet)
        if tx.tab_data.income_statement:
            missing += _detect_income_statement_gaps(tx.tab_data.income_statement)
        if not tx.tab_data.cash_flow:
            missing.append(MissingField.CASH_FLOW_TAB)

        overview = tx.tab_data.overview
        if not _is_null(getattr(overview, "charges", None)):
            pass
        elif MissingField.CIN not in missing:
            missing.append(MissingField.CHARGES)

        if not _is_null(getattr(overview, "legal_cases", None)):
            pass
        elif MissingField.CIN not in missing:
            missing.append(MissingField.LEGAL_CASES)

        always_manual = [
            (MissingField.BANK_STATEMENTS, "bank_statements"),
            (MissingField.CIBIL_REPORT, None),
            (MissingField.PROPERTY_TITLE_DEEDS, None),
            (MissingField.VALUATION_REPORT, None),
            (MissingField.LEGAL_OPINION_REPORT, None),
            (MissingField.ID_PROOF_DIRECTORS, None),
        ]
        for field, aux_key in always_manual:
            if aux_key and _has_aux(tx, aux_key):
                continue
            if field not in missing:
                missing.append(field)

        seen: set[MissingField] = set()
        deduped: list[MissingField] = []
        for f in missing:
            if f not in seen:
                seen.add(f)
                deduped.append(f)
        deduped.sort(key=lambda f: _FIELD_PRIORITY.get(f, 99))
        logger.info("Gap Detector: found %d missing fields → %s", len(deduped), [f.value for f in deduped])
        return {"missing_fields": deduped, "current_step": "gap_detector", "errors": errors}
    except GapDetectionError:
        raise
    except Exception as exc:
        msg = f"gap_detector unexpected error: {exc}"
        logger.exception(msg)
        errors.append(msg)
        return {"missing_fields": [], "current_step": "gap_detector", "errors": errors}
