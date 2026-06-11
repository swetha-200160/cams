"""
agents/bank_statement_agent.py
Agent 3.4 — Bank Statement Analyzer
Wave 2 (parallel): Requires bank_statements data from Agent 2.

In current PoC, bank_statements is an empty list in Agent 2 output.
Agent degrades gracefully with INSUFFICIENT status and a clear message.
When bank_statements are populated, this agent computes behaviour score.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from agent3_analysis.config import BANK_SCORE_WEIGHTS, MIN_MONTHS_BANK_STATEMENT
from agent3_analysis.schemas.input_schema import Agent2Output
from agent3_analysis.schemas.output_schema import (
    AgentStatus,
    Citation,
    DataQuality,
    BankingBehaviourReport,
)

logger = logging.getLogger(__name__)


def _score_banking_behaviour(
    avg_inflow: float,
    avg_outflow: float,
    cheque_bounces: int,
    large_cash_deposits: int,
    months: int,
    unusual_flags: List[str],
) -> float:
    """
    Compute a 0–100 banking behaviour score.

    Scoring logic:
    - Inflow stability: consistent inflow > outflow → max 30 pts
    - Cheque bounce penalty: each bounce deducts points (max 25 pts deduction)
    - Cash deposit risk: large cash deposits deduct points (max 20 pts)
    - Unusual patterns: each flag deducts points (max 25 pts)
    """
    score = 100.0

    # Inflow stability (deduct if outflow > inflow)
    if avg_inflow > 0:
        inflow_ratio = avg_outflow / avg_inflow
        if inflow_ratio > 1.0:
            deduction = min(BANK_SCORE_WEIGHTS["inflow_stability"] * 100, (inflow_ratio - 1.0) * 30)
            score -= deduction

    # Cheque bounce penalty (5 pts per bounce, capped at 25)
    bounce_deduction = min(cheque_bounces * 5, 25)
    score -= bounce_deduction

    # Large cash deposit risk (3 pts per instance, capped at 20)
    cash_deduction = min(large_cash_deposits * 3, 20)
    score -= cash_deduction

    # Unusual pattern flags (5 pts per flag, capped at 25)
    pattern_deduction = min(len(unusual_flags) * 5, 25)
    score -= pattern_deduction

    return round(max(0.0, score), 1)


def run(input_data: Agent2Output) -> BankingBehaviourReport:
    """
    Analyze bank statements for behaviour scoring.

    Args:
        input_data: Validated Agent 2 output.

    Returns:
        BankingBehaviourReport — INSUFFICIENT if no bank statements provided.
    """
    logger.info("Agent 3.4 — Bank Statement Analyzer started.")

    bank_statements = input_data.bank_statements or []

    if not bank_statements:
        logger.warning("Agent 3.4 — No bank statements provided. Returning INSUFFICIENT.")
        return BankingBehaviourReport(
            status=AgentStatus.SKIPPED,
            data_quality=DataQuality.INSUFFICIENT,
            narrative=(
                "Bank statement analysis skipped: no bank statement data provided by Agent 2. "
                "This section will be populated once 12–24 months of statements are available."
            ),
        )

    citations: List[Citation] = []

    # ── Detect format: transaction-level vs legacy month-aggregated ──
    # Transaction-level entries have a "date" field (from transformation agent).
    # Legacy entries have "inflow"/"outflow" keys (old month-summary format).
    first = bank_statements[0] if bank_statements else {}
    is_transaction_format = isinstance(first, dict) and "date" in first

    if is_transaction_format:
        # ── Aggregate transaction rows into monthly summaries ──────
        # Groups by YYYY-MM derived from transaction date.
        # month_data[month] = {inflow, outflow, bounces, cash_deposits}
        month_data: Dict[str, Dict] = defaultdict(lambda: {
            "inflow": 0.0, "outflow": 0.0,
            "cheque_bounces": 0, "large_cash_deposits": 0,
        })

        LARGE_CASH_THRESHOLD = 200_000   # ₹2 lakh: RBI reportable cash threshold

        for txn in bank_statements:
            if not isinstance(txn, dict):
                continue
            amount = txn.get("amount")
            if amount is None:
                continue
            amount = float(amount)

            # Derive month key from date (YYYY-MM-DD → YYYY-MM)
            date_str = txn.get("date") or ""
            month_key = date_str[:7] if len(date_str) >= 7 else "unknown"

            txn_type = (txn.get("type") or "").lower()
            if txn_type == "credit":
                month_data[month_key]["inflow"] += amount
            elif txn_type == "debit":
                month_data[month_key]["outflow"] += amount

            if txn.get("is_bounce"):
                month_data[month_key]["cheque_bounces"] += 1

            if txn.get("is_cash") and txn_type == "credit" and amount >= LARGE_CASH_THRESHOLD:
                month_data[month_key]["large_cash_deposits"] += 1

        # Remove the placeholder "unknown" month if real months exist
        if len(month_data) > 1:
            month_data.pop("unknown", None)

        bank_statements_aggregated = [
            {
                "month": month,
                "inflow": data["inflow"],
                "outflow": data["outflow"],
                "cheque_bounces": data["cheque_bounces"],
                "large_cash_deposits": data["large_cash_deposits"],
                "flags": [],
            }
            for month, data in sorted(month_data.items())
        ]
        logger.info(
            "Agent 3.4 — Aggregated %d transactions into %d month(s).",
            len(bank_statements), len(bank_statements_aggregated),
        )
    else:
        # ── Legacy format: already month-aggregated ────────────────
        # { "month": "YYYY-MM", "inflow": float, "outflow": float,
        #   "cheque_bounces": int, "large_cash_deposits": int, "flags": [str] }
        bank_statements_aggregated = bank_statements

    months = len(bank_statements_aggregated)

    if months < MIN_MONTHS_BANK_STATEMENT:
        logger.warning("Agent 3.4 — Only %d months of data. Minimum: %d.", months, MIN_MONTHS_BANK_STATEMENT)

    inflows: List[float] = []
    outflows: List[float] = []
    total_bounces = 0
    total_large_cash = 0
    unusual_flags: List[str] = []

    for entry in bank_statements_aggregated:
        if isinstance(entry, dict):
            inflows.append(float(entry.get("inflow", 0) or 0))
            outflows.append(float(entry.get("outflow", 0) or 0))
            total_bounces += int(entry.get("cheque_bounces", 0) or 0)
            total_large_cash += int(entry.get("large_cash_deposits", 0) or 0)
            unusual_flags.extend(entry.get("flags", []))

    avg_inflow: Optional[float] = sum(inflows) / len(inflows) if inflows else None
    avg_outflow: Optional[float] = sum(outflows) / len(outflows) if outflows else None

    behaviour_score: Optional[float] = None
    if avg_inflow is not None and avg_outflow is not None:
        behaviour_score = _score_banking_behaviour(
            avg_inflow, avg_outflow,
            total_bounces, total_large_cash,
            months, unusual_flags,
        )

    # Flags for narrative
    display_flags: List[str] = []
    if total_bounces > 0:
        display_flags.append(f"{total_bounces} cheque bounce(s) detected.")
    if total_large_cash > 0:
        display_flags.append(f"{total_large_cash} large cash deposit(s) detected.")
    display_flags.extend(unusual_flags)

    quality = DataQuality.COMPLETE if months >= MIN_MONTHS_BANK_STATEMENT else DataQuality.PARTIAL

    narrative = (
        f"Analyzed {months} months of bank statements. "
        f"Avg monthly inflow: {avg_inflow:,.0f}. Avg outflow: {avg_outflow:,.0f}. "
        f"Behaviour score: {behaviour_score}/100."
    ) if behaviour_score is not None else None

    logger.info(
        "Agent 3.4 — Completed. Months=%d, Score=%s, Bounces=%d.",
        months, behaviour_score, total_bounces,
    )

    return BankingBehaviourReport(
        status=AgentStatus.SUCCESS,
        data_quality=quality,
        behaviour_score=behaviour_score,
        avg_monthly_inflow=avg_inflow,
        avg_monthly_outflow=avg_outflow,
        cheque_bounce_count=total_bounces,
        large_cash_deposit_count=total_large_cash,
        unusual_pattern_flags=display_flags,
        narrative=narrative,
        citations=citations,
    )
