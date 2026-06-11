"""
agents/related_party_agent.py
Agent 3.8 — Related Party Detection Agent
Wave 2 (parallel): LLM-powered risk analysis on ROC filings, charges, and director data.

Uses Groq LLM to reason about:
- Open charges (especially multi-crore with active holders)
- Director overlaps and cross-company exposure
- Related party transaction signals from available data
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from agent3_analysis.schemas.input_schema import Agent2Output
from agent3_analysis.schemas.output_schema import (
    AgentStatus,
    Citation,
    DataQuality,
    RelatedPartyReport,
)
from agent3_analysis.utils.groq_client import call_groq_json
from agent3_analysis.utils.financial_utils import parse_amount

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a credit risk analyst specializing in related party transaction detection
and corporate governance risk assessment for Indian companies.

You will be given structured company data and must identify:
1. Risk indicators from open charges (large, undischarged loan obligations)
2. Director-level risk signals (multiple directorships, foreign directors in Indian SMEs)
3. Related party transaction indicators
4. Any governance red flags

Respond ONLY with a valid JSON object in this exact format:
{
  "risk_alerts": ["alert1", "alert2"],
  "related_party_transactions_detected": false,
  "director_risk_flags": ["flag1"],
  "open_charges_summary": ["charge1"],
  "narrative": "One paragraph summary."
}

Do NOT include any text outside the JSON object."""


def run(input_data: Agent2Output, groq_api_key: str) -> RelatedPartyReport:
    """
    Detect related party risks and governance flags using LLM reasoning.

    Args:
        input_data: Validated Agent 2 output.
        groq_api_key: Groq API key.

    Returns:
        RelatedPartyReport with risk alerts and narrative.
    """
    logger.info("Agent 3.8 — Related Party Detection started.")

    overview = input_data.enriched_overview
    citations: List[Citation] = []
    open_charges: List[Dict[str, Any]] = []

    if not overview:
        logger.warning("Agent 3.8 — No enriched_overview. Skipping.")
        return RelatedPartyReport(
            status=AgentStatus.SKIPPED,
            data_quality=DataQuality.INSUFFICIENT,
            narrative="Related party analysis skipped: no company overview data available.",
        )

    # --- Identify open charges ---
    for charge in (overview.charges or []):
        amount = parse_amount(charge.amount)
        is_open = not charge.closure_date or charge.closure_date.strip() == ""
        if is_open:
            open_charges.append({
                "charge_id": charge.charge_id,
                "holder": charge.holder,
                "amount": charge.amount,
                "amount_parsed": amount,
                "creation_date": charge.creation_date,
                "status": "OPEN",
            })
            citations.append(Citation(
                document="enriched_overview",
                field=f"charge_{charge.charge_id}",
                source="ZAUBA",
            ))

    # --- Build LLM payload ---
    payload = {
        "company_name": overview.company_name,
        "cin": overview.cin,
        "pan": overview.pan,
        "incorporation_date": overview.incorporation_date,
        "industry": overview.industry,
        "directors": overview.directors or [],
        "charges": [c.model_dump() for c in (overview.charges or [])],
        "open_charges": open_charges,
        "legal_cases": overview.legal_cases or [],
        "roc_filings": input_data.roc_filings or [],
        "networth": overview.networth,
        "total_debt": overview.total_debt,
    }

    user_prompt = (
        f"Analyze this Indian company data for related party risks and governance flags:\n\n"
        f"{json.dumps(payload, indent=2)}"
    )

    try:
        result = call_groq_json(
            api_key=groq_api_key,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        risk_alerts = result.get("risk_alerts", [])
        related_party_detected = bool(result.get("related_party_transactions_detected", False))
        director_flags = result.get("director_risk_flags", [])
        narrative = result.get("narrative")

        quality = DataQuality.COMPLETE if overview.charges else DataQuality.PARTIAL

        logger.info(
            "Agent 3.8 — Completed. Risk alerts=%d. Open charges=%d. RPT detected=%s.",
            len(risk_alerts), len(open_charges), related_party_detected,
        )

        return RelatedPartyReport(
            status=AgentStatus.SUCCESS,
            data_quality=quality,
            risk_alerts=risk_alerts,
            related_party_transactions_detected=related_party_detected,
            open_charges=open_charges,
            director_risk_flags=director_flags,
            narrative=narrative,
            citations=citations,
        )

    except Exception as exc:
        logger.error("Agent 3.8 — LLM call failed: %s", exc, exc_info=True)

        # Fallback: rule-based flags without LLM
        fallback_alerts = []
        if open_charges:
            for charge in open_charges:
                fallback_alerts.append(
                    f"Open charge of {charge['amount']} held by {charge['holder']} "
                    f"(created {charge['creation_date']}) — not discharged."
                )
        if overview.networth is not None and overview.networth < 0:
            fallback_alerts.append("Negative networth detected — potential solvency concern.")

        return RelatedPartyReport(
            status=AgentStatus.PARTIAL,
            data_quality=DataQuality.PARTIAL,
            risk_alerts=fallback_alerts,
            open_charges=open_charges,
            narrative=f"LLM analysis unavailable ({exc}). Rule-based flags applied.",
            citations=citations,
        )
