"""
agents/market_risk_agent.py
Agent 3.10 — Market Risk Agent
Wave 2 (parallel): LLM-powered macroeconomic and sector risk assessment.

Evaluates macro risks, sector volatility, interest rate sensitivity,
and regulatory risk based on company profile and industry classification.
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
    MarketRiskReport,
)
from agent3_analysis.utils.groq_client import call_groq_json

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a macroeconomic risk analyst specializing in Indian credit markets.

Given a company's industry, financial profile, and debt exposure, evaluate:
1. Macro risks relevant to this sector (interest rates, inflation, FX, regulatory)
2. Sector volatility level: low / moderate / high
3. Interest rate sensitivity (given total_debt and finance_cost)
4. Regulatory risk specific to the NIC classification
5. Overall market risk narrative

Respond ONLY with a valid JSON object:
{
  "macro_risks": ["risk1", "risk2"],
  "sector_volatility": "low|moderate|high",
  "interest_rate_sensitivity": "low|moderate|high — brief reason",
  "regulatory_risk": "low|moderate|high — brief reason",
  "narrative": "One paragraph macro risk summary."
}

Do NOT include any text outside the JSON object."""


def run(input_data: Agent2Output, groq_api_key: str) -> MarketRiskReport:
    """
    Generate macroeconomic and market risk assessment using LLM.

    Args:
        input_data: Validated Agent 2 output.
        groq_api_key: Groq API key.

    Returns:
        MarketRiskReport.
    """
    logger.info("Agent 3.10 — Market Risk started.")

    overview = input_data.enriched_overview
    citations: List[Citation] = []

    if not overview:
        logger.warning("Agent 3.10 — No enriched_overview. Skipping.")
        return MarketRiskReport(
            status=AgentStatus.SKIPPED,
            data_quality=DataQuality.INSUFFICIENT,
            narrative="Market risk assessment skipped: no company overview data available.",
        )

    # Finance cost from most recent income statement
    finance_cost = None
    if input_data.income_statement:
        sorted_is = sorted(
            [e for e in input_data.income_statement if e.year],
            key=lambda x: str(x.year),
            reverse=True,
        )
        if sorted_is:
            finance_cost = sorted_is[0].finance_cost

    payload = {
        "company_name": overview.company_name,
        "industry": overview.industry,
        "total_debt": overview.total_debt,
        "networth": overview.networth,
        "ebitda": overview.ebitda,
        "finance_cost": finance_cost,
        "net_sales": overview.net_sales,
        "metrics_year": overview.metrics_year_income,
        "open_charges_count": sum(
            1 for c in (overview.charges or [])
            if not c.closure_date or c.closure_date.strip() == ""
        ),
        "legal_cases_count": len(overview.legal_cases or []),
    }

    citations.append(Citation(
        document="enriched_overview",
        field="market_risk_inputs",
        year=overview.metrics_year_income,
        source="ZAUBA",
    ))

    user_prompt = (
        f"Assess the macroeconomic and market risks for this Indian company:\n\n"
        f"{json.dumps(payload, indent=2)}"
    )

    try:
        result = call_groq_json(
            api_key=groq_api_key,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        logger.info("Agent 3.10 — Completed successfully.")

        return MarketRiskReport(
            status=AgentStatus.SUCCESS,
            data_quality=DataQuality.COMPLETE,
            macro_risks=result.get("macro_risks", []),
            sector_volatility=result.get("sector_volatility"),
            interest_rate_sensitivity=result.get("interest_rate_sensitivity"),
            regulatory_risk=result.get("regulatory_risk"),
            narrative=result.get("narrative"),
            citations=citations,
        )

    except Exception as exc:
        logger.error("Agent 3.10 — LLM call failed: %s", exc, exc_info=True)
        return MarketRiskReport(
            status=AgentStatus.PARTIAL,
            data_quality=DataQuality.PARTIAL,
            narrative=f"Market risk LLM analysis unavailable ({exc}).",
            citations=citations,
        )
