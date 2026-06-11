"""
state.py
Shared LangGraph state for Agent 3 orchestration.

All sub-agents read input from and write outputs to this state object.
LangGraph passes state through the graph — no inter-agent direct calls.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict

from agent3_analysis.schemas.input_schema import Agent2Output
from agent3_analysis.schemas.output_schema import (
    BankingBehaviourReport,
    CashFlowProjection,
    GSTAnalyticsReport,
    IndustryIntelligenceReport,
    InsightsOutput,
    MarketRiskReport,
    ParsedFinancials,
    RatioReport,
    RelatedPartyReport,
    TaxComplianceReport,
    TrendReport,
)


class AgentState(TypedDict, total=False):
    """
    Shared state object passed through the LangGraph StateGraph.

    Fields are populated progressively as each wave completes.
    All output fields are Optional — downstream agents must check
    for None before consuming upstream results.
    """

    # --- Input ---
    input_data: Agent2Output          # Raw Agent 2 output — immutable throughout graph
    groq_api_key: str                 # Injected at invocation time, never logged

    # --- Wave 1 output ---
    parsed_financials: Optional[ParsedFinancials]

    # --- Wave 2 outputs (populated in parallel) ---
    ratio_report: Optional[RatioReport]
    trend_report: Optional[TrendReport]
    banking_behaviour: Optional[BankingBehaviourReport]
    gst_analytics: Optional[GSTAnalyticsReport]
    tax_compliance: Optional[TaxComplianceReport]
    related_party: Optional[RelatedPartyReport]
    industry_intelligence: Optional[IndustryIntelligenceReport]
    market_risk: Optional[MarketRiskReport]

    # --- Wave 3 output ---
    cash_flow_projection: Optional[CashFlowProjection]

    # --- Final merged output ---
    insights_output: Optional[InsightsOutput]

    # --- Execution tracking (populated by node_merge) ---
    agents_executed: List[str]
    agents_skipped: List[str]
    agents_failed: List[str]
    errors: List[str]
