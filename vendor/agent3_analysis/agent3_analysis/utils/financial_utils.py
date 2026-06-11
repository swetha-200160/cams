"""
utils/financial_utils.py
Shared financial computation helpers.
All functions return None on insufficient data rather than raising — callers
must handle None and set data_quality accordingly.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# String / value normalization
# ---------------------------------------------------------------------------

def parse_amount(value: Any) -> Optional[float]:
    """
    Parse a monetary string like "1,175,000,000.00" into float.
    Returns None if value is None, empty, or unparseable.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[,\s₹$]", "", str(value)).strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        logger.warning("Cannot parse amount: %s", value)
        return None


def safe_divide(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """
    Safe division returning None on zero denominator or None inputs.
    """
    if numerator is None or denominator is None:
        return None
    if denominator == 0.0:
        logger.debug("Division by zero — returning None.")
        return None
    return round(numerator / denominator, 4)


def round_or_none(value: Optional[float], decimals: int = 2) -> Optional[float]:
    """Round float or return None."""
    if value is None:
        return None
    return round(value, decimals)


# ---------------------------------------------------------------------------
# Ratio calculations
# ---------------------------------------------------------------------------

def calc_ebitda_margin(ebitda: Optional[float], revenue: Optional[float]) -> Optional[float]:
    """EBITDA / Revenue."""
    return safe_divide(ebitda, revenue)


def calc_net_profit_margin(pat: Optional[float], revenue: Optional[float]) -> Optional[float]:
    """PAT / Revenue."""
    return safe_divide(pat, revenue)


def calc_debt_to_equity(total_debt: Optional[float], networth: Optional[float]) -> Optional[float]:
    """
    Total Debt / Networth.
    Negative networth → returns None and logs warning (technically undefined / infinite).
    """
    if networth is not None and networth <= 0:
        logger.warning("Networth is non-positive (%s) — D/E ratio undefined.", networth)
        return None
    return safe_divide(total_debt, networth)


def calc_interest_coverage(ebitda: Optional[float], finance_cost: Optional[float]) -> Optional[float]:
    """EBITDA / Interest (Finance Cost)."""
    return safe_divide(ebitda, finance_cost)


def calc_dscr(
    operating_cash_flow: Optional[float],
    total_debt_service: Optional[float],
) -> Optional[float]:
    """
    DSCR = Operating Cash Flow / Total Debt Service (principal + interest).
    """
    return safe_divide(operating_cash_flow, total_debt_service)


def calc_gross_profit_margin(
    revenue: Optional[float],
    cost_of_material: Optional[float],
) -> Optional[float]:
    """(Revenue - CoM) / Revenue."""
    if revenue is None or cost_of_material is None:
        return None
    gross_profit = revenue - cost_of_material
    return safe_divide(gross_profit, revenue)


# ---------------------------------------------------------------------------
# Trend helpers
# ---------------------------------------------------------------------------

def yoy_growth(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """
    Year-over-year growth rate as a decimal.
    Returns None if either value is missing or previous is zero.
    """
    if current is None or previous is None or previous == 0.0:
        return None
    return round_or_none((current - previous) / abs(previous), 4)


def build_trend_series(
    records: List[Dict[str, Any]],
    value_key: str,
    year_key: str = "year",
) -> List[Dict[str, Any]]:
    """
    Build a sorted trend series with YoY growth from a list of year-keyed records.

    Args:
        records: List of dicts with year and value fields.
        value_key: Key to extract value from each record.
        year_key: Key for the year field.

    Returns:
        List of {year, value, yoy_growth} sorted by year ascending.
    """
    valid = [
        {"year": r[year_key], "value": r[value_key]}
        for r in records
        if r.get(year_key) and r.get(value_key) is not None
    ]
    valid.sort(key=lambda x: str(x["year"]))

    result = []
    for i, item in enumerate(valid):
        prev_value = valid[i - 1]["value"] if i > 0 else None
        result.append({
            "year": item["year"],
            "value": item["value"],
            "yoy_growth": yoy_growth(item["value"], prev_value),
        })
    return result


# ---------------------------------------------------------------------------
# Data quality helpers
# ---------------------------------------------------------------------------

def count_nulls(data: Dict[str, Any], fields: List[str]) -> List[str]:
    """
    Return list of field names from `fields` that are None in `data`.
    Used to populate null_field_warnings in ParsedFinancials.
    """
    return [f for f in fields if data.get(f) is None]
