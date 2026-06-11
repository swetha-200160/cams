"""
agents/industry_agent.py
Agent 3.9 — Industry Intelligence Agent
Wave 2 (parallel): LLM-powered analysis of industry classification and risks.

Uses NIC code + industry description from enriched_overview to generate:
- Growth rate estimates
- Industry-specific risk factors
- Competitive landscape overview
"""

from __future__ import annotations

import json
import logging
from typing import List

from agent3_analysis.schemas.input_schema import Agent2Output
from agent3_analysis.schemas.output_schema import (
    AgentStatus,
    Citation,
    DataQuality,
    IndustryIntelligenceReport,
)
from agent3_analysis.utils.groq_client import call_groq_json

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an industry analyst specializing in Indian business sectors.

Given a company's NIC industry classification and basic financial profile, provide:
1. Estimated annual growth rate for this industry segment in India
2. Key industry risks (regulatory, competitive, cyclical, technological)
3. Brief competitive landscape overview
4. How this company's financial scale compares to typical sector players

Respond ONLY with a valid JSON object:
{
  "industry_classification": "Brief cleaned classification name",
  "growth_rate_estimate": "e.g. 8-12% CAGR",
  "industry_risks": ["risk1", "risk2", "risk3"],
  "competitive_landscape": "One paragraph.",
  "narrative": "One paragraph summary of industry positioning."
}

Do NOT include any text outside the JSON object."""


def run(input_data: Agent2Output, groq_api_key: str) -> IndustryIntelligenceReport:
    """
    Generate industry intelligence report using LLM.

    Args:
        input_data: Validated Agent 2 output.
        groq_api_key: Groq API key.

    Returns:
        IndustryIntelligenceReport.
    """
    logger.info("Agent 3.9 — Industry Intelligence started.")

    overview = input_data.enriched_overview
    citations: List[Citation] = []

    if not overview or not overview.industry:
        logger.warning("Agent 3.9 — No industry data. Skipping.")
        return IndustryIntelligenceReport(
            status=AgentStatus.SKIPPED,
            data_quality=DataQuality.INSUFFICIENT,
            narrative="Industry intelligence skipped: no industry classification available.",
        )

    citations.append(Citation(
        document="enriched_overview",
        field="industry",
        source="ZAUBA",
    ))

    payload = {
        "industry_raw": overview.industry,
        "company_name": overview.company_name,
        "incorporation_date": overview.incorporation_date,
        "registered_address": overview.registered_address,
        "net_sales": overview.net_sales,
        "ebitda": overview.ebitda,
        "pat": overview.pat,
        "total_debt": overview.total_debt,
        "metrics_year": overview.metrics_year_income,
    }

    user_prompt = (
        f"Analyze the industry intelligence for this Indian company:\n\n"
        f"{json.dumps(payload, indent=2)}"
    )

    try:
        result = call_groq_json(
            api_key=groq_api_key,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        logger.info("Agent 3.9 — Completed successfully.")

        return IndustryIntelligenceReport(
            status=AgentStatus.SUCCESS,
            data_quality=DataQuality.COMPLETE,
            industry_classification=result.get("industry_classification", overview.industry),
            growth_rate_estimate=result.get("growth_rate_estimate"),
            industry_risks=result.get("industry_risks", []),
            competitive_landscape=result.get("competitive_landscape"),
            narrative=result.get("narrative"),
            citations=citations,
        )

    except Exception as exc:
        logger.error("Agent 3.9 — LLM call failed: %s", exc, exc_info=True)
        return IndustryIntelligenceReport(
            status=AgentStatus.PARTIAL,
            data_quality=DataQuality.PARTIAL,
            industry_classification=overview.industry,
            narrative=f"Industry LLM analysis unavailable ({exc}). Raw classification retained.",
            citations=citations,
        )
