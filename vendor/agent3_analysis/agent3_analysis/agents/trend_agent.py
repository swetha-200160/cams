"""
agents/trend_agent.py
Agent 3.3 — Trend Analysis Agent
Wave 2 (parallel): Requires parsed income_statement with >= MIN_YEARS_FOR_TREND entries.

Identifies YoY growth patterns, anomalies, and declining/improving metrics.
"""

from __future__ import annotations

import logging
from typing import List

from agent3_analysis.config import MIN_YEARS_FOR_TREND
from agent3_analysis.schemas.input_schema import Agent2Output
from agent3_analysis.schemas.output_schema import (
    AgentStatus,
    Citation,
    DataQuality,
    TrendReport,
)
from agent3_analysis.utils.financial_utils import build_trend_series

logger = logging.getLogger(__name__)

# YoY growth threshold for flagging anomaly (e.g. -50% or +200%)
_ANOMALY_DROP_THRESHOLD = -0.50
_ANOMALY_SPIKE_THRESHOLD = 2.00


def run(input_data: Agent2Output) -> TrendReport:
    """
    Perform multi-year trend analysis on income statement data.

    Args:
        input_data: Validated Agent 2 output.

    Returns:
        TrendReport with trend series, anomalies, and directional insights.
    """
    logger.info("Agent 3.3 — Trend Analysis started.")

    citations: List[Citation] = []
    income_stmt = input_data.income_statement or []

    if len(income_stmt) < MIN_YEARS_FOR_TREND:
        logger.warning(
            "Agent 3.3 — Only %d year(s) of income data. Minimum required: %d.",
            len(income_stmt), MIN_YEARS_FOR_TREND,
        )
        return TrendReport(
            status=AgentStatus.SKIPPED,
            data_quality=DataQuality.INSUFFICIENT,
            narrative=(
                f"Trend analysis requires at least {MIN_YEARS_FOR_TREND} years of data. "
                f"Only {len(income_stmt)} year(s) available."
            ),
        )

    # Collect source citations
    docs_seen = set()
    for entry in income_stmt:
        for doc in (entry.source_documents or []):
            if doc not in docs_seen:
                citations.append(Citation(document=doc, field="income_statement", year=entry.year))
                docs_seen.add(doc)

    # --- Build trend series ---
    is_dicts = [e.model_dump() for e in income_stmt]

    revenue_trend = build_trend_series(is_dicts, "revenue_from_operations")
    ebitda_trend: List = []     # EBITDA not directly in IS; computed via overview — left for future
    pat_trend: List = []        # PAT not directly in IS rows either

    # --- Anomaly detection ---
    anomalies: List[str] = []
    improving: List[str] = []
    declining: List[str] = []

    def _evaluate_series(series: List, metric_name: str) -> None:
        for item in series:
            growth = item.get("yoy_growth")
            if growth is None:
                continue
            if growth <= _ANOMALY_DROP_THRESHOLD:
                anomalies.append(
                    f"{metric_name} dropped {growth:.1%} in {item['year']} — significant decline detected."
                )
                declining.append(metric_name)
            elif growth >= _ANOMALY_SPIKE_THRESHOLD:
                anomalies.append(
                    f"{metric_name} spiked {growth:.1%} in {item['year']} — unusual growth detected."
                )
            elif growth > 0.05:
                if metric_name not in improving:
                    improving.append(metric_name)
            elif growth < -0.05:
                if metric_name not in declining:
                    declining.append(metric_name)

    _evaluate_series(revenue_trend, "Revenue")

    # --- Narrative ---
    narrative_parts = []
    if revenue_trend:
        years = [r["year"] for r in revenue_trend]
        values = [r["value"] for r in revenue_trend if r["value"] is not None]
        if values:
            narrative_parts.append(
                f"Revenue data available for years: {', '.join(years)}. "
                f"Range: {min(values):,.0f} to {max(values):,.0f}."
            )
    if anomalies:
        narrative_parts.append(f"Anomalies detected: {'; '.join(anomalies)}")
    if improving:
        narrative_parts.append(f"Improving metrics: {', '.join(set(improving))}.")
    if declining:
        narrative_parts.append(f"Declining metrics: {', '.join(set(declining))}.")

    narrative = " ".join(narrative_parts) if narrative_parts else None

    quality = DataQuality.COMPLETE if len(income_stmt) >= 3 else DataQuality.PARTIAL

    logger.info(
        "Agent 3.3 — Trend analysis complete. Revenue points=%d. Anomalies=%d.",
        len(revenue_trend), len(anomalies),
    )

    return TrendReport(
        status=AgentStatus.SUCCESS,
        data_quality=quality,
        revenue_trend=revenue_trend,
        ebitda_trend=ebitda_trend,
        pat_trend=pat_trend,
        anomalies=anomalies,
        improving_metrics=list(set(improving)),
        declining_metrics=list(set(declining)),
        narrative=narrative,
        citations=citations,
    )
