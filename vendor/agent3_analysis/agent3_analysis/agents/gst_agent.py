"""
agents/gst_agent.py
Agent 3.6 — GST Analytics Agent
Wave 2 (parallel): Compares GST-reported sales with financial statement revenue.

In PoC, gst_returns is empty. Degrades gracefully.
When populated: cross-validates GSTIN-reported sales against P&L revenue.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from agent3_analysis.config import GST_DISCREPANCY_THRESHOLD
from agent3_analysis.schemas.input_schema import Agent2Output
from agent3_analysis.schemas.output_schema import (
    AgentStatus,
    Citation,
    DataQuality,
    GSTAnalyticsReport,
)
from agent3_analysis.utils.financial_utils import safe_divide

logger = logging.getLogger(__name__)


def run(input_data: Agent2Output) -> GSTAnalyticsReport:
    """
    Compare GST-declared sales against income statement revenue.

    Args:
        input_data: Validated Agent 2 output.

    Returns:
        GSTAnalyticsReport — INSUFFICIENT if no GST returns provided.
    """
    logger.info("Agent 3.6 — GST Analytics started.")

    gst_returns = input_data.gst_returns or []
    overview = input_data.enriched_overview
    citations: List[Citation] = []

    if not gst_returns:
        logger.warning("Agent 3.6 — No GST returns provided. Returning INSUFFICIENT.")
        gstin_note = f" (GSTIN: {overview.gstin})" if overview and overview.gstin else ""
        return GSTAnalyticsReport(
            status=AgentStatus.SKIPPED,
            data_quality=DataQuality.INSUFFICIENT,
            narrative=(
                f"GST analytics skipped: no GST return data provided by Agent 2{gstin_note}. "
                "Revenue authenticity score will be computed once GSTR-1/GSTR-3B data is available."
            ),
        )

    # ── Detect format: rich gst_data vs legacy year-summary ──────
    first = gst_returns[0] if gst_returns else {}
    is_rich_format = isinstance(first, dict) and first.get("__rich_gst_data__")

    if is_rich_format:
        # ── Rich format: consolidated gst_data from transformation agent ──
        gst_data = {k: v for k, v in first.items() if k != "__rich_gst_data__"}

        period     = gst_data.get("period") or "unknown"
        gst_sales  = gst_data.get("gst_sales") or {}
        annual_txv = gst_sales.get("annual_taxable_value")

        total_gst_sales  = annual_txv
        risk_flags       = gst_data.get("risk_flags") or {}
        consistency      = gst_data.get("gst_consistency") or {}
        discrepancy_flag = risk_flags.get("gstr_mismatch", False)

        # Cross-validate against P&L income statement if available
        pl_revenue_by_year = {
            e.year: e.revenue_from_operations
            for e in (input_data.income_statement or [])
            if e.year and e.revenue_from_operations is not None
        }

        total_pl_sales: Optional[float] = None
        discrepancy_pct: Optional[float] = None
        discrepancies: List[str] = []

        if total_gst_sales and pl_revenue_by_year:
            # Use the P&L year whose revenue is closest to GST annual sales
            # (avoids false mismatches when provisional/annualised years are present)
            total_pl_sales = min(
                pl_revenue_by_year.values(),
                key=lambda rev: abs(rev - total_gst_sales),
            )
            discrepancy_pct = safe_divide(abs(total_gst_sales - total_pl_sales), total_pl_sales)
            if discrepancy_pct is not None and discrepancy_pct > GST_DISCREPANCY_THRESHOLD:
                discrepancy_flag = True
                discrepancies.append(
                    f"GST annual sales ({total_gst_sales:,.0f}) differ from "
                    f"P&L revenue ({total_pl_sales:,.0f}) by {discrepancy_pct:.1%}."
                )

        # Additional risk flag discrepancies
        if risk_flags.get("abnormal_spike_detected"):
            discrepancies.append("Abnormal monthly sales spike detected in GST returns.")
        if risk_flags.get("sudden_drop_detected"):
            discrepancies.append("Sudden monthly sales drop detected in GST returns.")
        if risk_flags.get("tax_inconsistency"):
            discrepancies.append("Tax component totals do not reconcile with total_tax_paid.")

        revenue_authenticity_score: Optional[float] = None
        if discrepancy_pct is not None:
            revenue_authenticity_score = round(max(0.0, 100 - discrepancy_pct * 100), 1)
        elif total_gst_sales:
            # No P&L to compare — base score on internal consistency
            flag_count = sum(1 for v in risk_flags.values() if v)
            revenue_authenticity_score = round(max(0.0, 100 - flag_count * 10), 1)

        quality = DataQuality.COMPLETE if total_gst_sales is not None else DataQuality.PARTIAL

        logger.info(
            "Agent 3.6 — Rich format. Period=%s. Score=%s. Flags=%s.",
            period, revenue_authenticity_score, risk_flags,
        )

        return GSTAnalyticsReport(
            status=AgentStatus.SUCCESS,
            data_quality=quality,
            revenue_authenticity_score=revenue_authenticity_score,
            gst_reported_sales=total_gst_sales,
            financial_reported_sales=total_pl_sales,
            discrepancy_pct=discrepancy_pct,
            discrepancy_flag=discrepancy_flag,
            narrative=(
                f"GST analytics completed for period {period}. "
                f"Annual taxable value: {total_gst_sales:,.0f}. "
                f"Revenue authenticity score: {revenue_authenticity_score}/100. "
                + (" ".join(discrepancies) if discrepancies else "No discrepancies detected.")
            ),
            citations=citations,
        )

    # ── Legacy format: [{year, total_taxable_sales, source}] ──────
    gst_sales_by_year = {
        r.get("year"): float(r.get("total_taxable_sales", 0) or 0)
        for r in gst_returns
        if isinstance(r, dict) and r.get("year")
    }

    pl_revenue_by_year = {
        e.year: e.revenue_from_operations
        for e in (input_data.income_statement or [])
        if e.year and e.revenue_from_operations is not None
    }

    discrepancies = []
    total_gst_sales = None
    total_pl_sales = None
    discrepancy_pct = None
    discrepancy_flag = False

    common_years = set(gst_sales_by_year.keys()) & set(pl_revenue_by_year.keys())

    if common_years:
        total_gst_sales = sum(gst_sales_by_year[y] for y in common_years)
        total_pl_sales = sum(pl_revenue_by_year[y] for y in common_years)
        discrepancy_pct = safe_divide(abs(total_gst_sales - total_pl_sales), total_pl_sales)

        if discrepancy_pct is not None and discrepancy_pct > GST_DISCREPANCY_THRESHOLD:
            discrepancy_flag = True
            discrepancies.append(
                f"GST-declared sales ({total_gst_sales:,.0f}) differ from "
                f"P&L revenue ({total_pl_sales:,.0f}) by {discrepancy_pct:.1%} — "
                f"exceeds {GST_DISCREPANCY_THRESHOLD:.0%} threshold."
            )

    revenue_authenticity_score = None
    if discrepancy_pct is not None:
        revenue_authenticity_score = round(max(0.0, 100 - discrepancy_pct * 100), 1)
    elif common_years:
        revenue_authenticity_score = 100.0

    if overview and overview.gstin:
        citations.append(Citation(
            document="enriched_overview",
            field="gstin",
            source="ZAUBA",
        ))

    narrative = (
        f"GST analytics completed for {len(common_years)} overlapping year(s). "
        f"Revenue authenticity score: {revenue_authenticity_score}/100. "
        + (" ".join(discrepancies) if discrepancies else "No discrepancies detected.")
    ) if common_years else "No overlapping years between GST data and income statement."

    quality = DataQuality.COMPLETE if revenue_authenticity_score is not None else DataQuality.PARTIAL

    logger.info(
        "Agent 3.6 — Completed. Authenticity score=%s. Discrepancy flag=%s.",
        revenue_authenticity_score, discrepancy_flag,
    )

    return GSTAnalyticsReport(
        status=AgentStatus.SUCCESS,
        data_quality=quality,
        revenue_authenticity_score=revenue_authenticity_score,
        gst_reported_sales=total_gst_sales,
        financial_reported_sales=total_pl_sales,
        discrepancy_pct=discrepancy_pct,
        discrepancy_flag=discrepancy_flag,
        narrative=narrative,
        citations=citations,
    )
