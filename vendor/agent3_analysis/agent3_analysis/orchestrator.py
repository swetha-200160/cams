"""
orchestrator.py
LangGraph StateGraph orchestrator for Agent 3 — Analysis Agent.

Execution waves:
  Wave 1 (sequential):  parser_agent (3.1)
  Wave 2 (parallel):    ratio, trend, bank_statement, gst, tax_compliance,
                        related_party, industry, market_risk (3.2–3.4, 3.6–3.10)
  Wave 3 (sequential):  cash_flow (3.5) — depends on parsed_financials + banking_behaviour
  Final:                merge_results → InsightsOutput
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from langgraph.graph import END, StateGraph

from agent3_analysis.agents import (
    bank_statement_agent,
    cash_flow_agent,
    gst_agent,
    industry_agent,
    market_risk_agent,
    parser_agent,
    ratio_agent,
    related_party_agent,
    tax_compliance_agent,
    trend_agent,
)
from agent3_analysis.schemas.input_schema import Agent2Output
from agent3_analysis.schemas.output_schema import AgentStatus, InsightsOutput
from agent3_analysis.state import AgentState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node functions — each wraps one sub-agent with error isolation
# ---------------------------------------------------------------------------

def _safe_run(agent_name: str, fn, *args, **kwargs):
    """
    Execute a sub-agent function with full error isolation.
    Returns (result, error_str) — caller writes to its own state key.
    """
    try:
        result = fn(*args, **kwargs)
        logger.info("✓ %s completed — status: %s", agent_name, result.status)
        return result, None
    except Exception as exc:
        logger.error("✗ %s failed: %s", agent_name, exc, exc_info=True)
        return None, f"{agent_name}: {exc}"


# ---------------------------------------------------------------------------
# Each node returns ONLY the keys it owns — prevents LangGraph concurrent
# write conflicts (InvalidUpdateError) when Wave 2 nodes run in parallel.
# Execution tracking (agents_executed, agents_failed, errors) is collected
# in the merge node from each section's status field instead.
# ---------------------------------------------------------------------------

# --- Wave 1 ---

def node_parser(state: AgentState) -> dict:
    """Agent 3.1 — Financial Statement Parser (Wave 1 foundation)."""
    logger.info("=== Wave 1: Parser Agent ===")
    result, _ = _safe_run("parser_agent", parser_agent.run, state["input_data"])
    return {"parsed_financials": result}


# --- Wave 2 nodes — each owns exactly one output key ---

def node_ratio(state: AgentState) -> dict:
    """Agent 3.2 — Ratio Analysis."""
    result, _ = _safe_run("ratio_agent", ratio_agent.run, state["input_data"])
    return {"ratio_report": result}


def node_trend(state: AgentState) -> dict:
    """Agent 3.3 — Trend Analysis."""
    result, _ = _safe_run("trend_agent", trend_agent.run, state["input_data"])
    return {"trend_report": result}


def node_bank_statement(state: AgentState) -> dict:
    """Agent 3.4 — Bank Statement Analyzer."""
    result, _ = _safe_run("bank_statement_agent", bank_statement_agent.run, state["input_data"])
    return {"banking_behaviour": result}


def node_gst(state: AgentState) -> dict:
    """Agent 3.6 — GST Analytics."""
    result, _ = _safe_run("gst_agent", gst_agent.run, state["input_data"])
    return {"gst_analytics": result}


def node_tax_compliance(state: AgentState) -> dict:
    """Agent 3.7 — Tax Compliance."""
    result, _ = _safe_run("tax_compliance_agent", tax_compliance_agent.run, state["input_data"])
    return {"tax_compliance": result}


def node_related_party(state: AgentState) -> dict:
    """Agent 3.8 — Related Party Detection (LLM)."""
    result, _ = _safe_run(
        "related_party_agent",
        related_party_agent.run,
        state["input_data"],
        state["groq_api_key"],
    )
    return {"related_party": result}


def node_industry(state: AgentState) -> dict:
    """Agent 3.9 — Industry Intelligence (LLM)."""
    result, _ = _safe_run(
        "industry_agent",
        industry_agent.run,
        state["input_data"],
        state["groq_api_key"],
    )
    return {"industry_intelligence": result}


def node_market_risk(state: AgentState) -> dict:
    """Agent 3.10 — Market Risk (LLM)."""
    result, _ = _safe_run(
        "market_risk_agent",
        market_risk_agent.run,
        state["input_data"],
        state["groq_api_key"],
    )
    return {"market_risk": result}


# --- Wave 3 ---

def node_cash_flow(state: AgentState) -> dict:
    """Agent 3.5 — Cash Flow (Wave 3: depends on parser + bank_statement)."""
    logger.info("=== Wave 3: Cash Flow Agent ===")

    from agent3_analysis.schemas.output_schema import (
        AgentStatus as AS,
        BankingBehaviourReport,
        DataQuality,
        ParsedFinancials,
    )

    parsed = state.get("parsed_financials") or ParsedFinancials(
        status=AS.SKIPPED, data_quality=DataQuality.INSUFFICIENT,
    )
    banking = state.get("banking_behaviour") or BankingBehaviourReport(
        status=AS.SKIPPED, data_quality=DataQuality.INSUFFICIENT,
    )

    result, _ = _safe_run(
        "cash_flow_agent",
        cash_flow_agent.run,
        state["input_data"],
        parsed,
        banking,
    )
    return {"cash_flow_projection": result}


# --- Final merge ---

def node_merge(state: AgentState) -> dict:
    """
    Inspect all sub-agent outputs, build execution summary, emit InsightsOutput.
    This is the only node allowed to write the insights_output key.
    """
    logger.info("=== Merging all agent outputs ===")

    # Derive execution tracking from section statuses
    agents_executed, agents_skipped, agents_failed, errors = [], [], [], []

    section_map = {
        "parser_agent":          state.get("parsed_financials"),
        "ratio_agent":           state.get("ratio_report"),
        "trend_agent":           state.get("trend_report"),
        "bank_statement_agent":  state.get("banking_behaviour"),
        "cash_flow_agent":       state.get("cash_flow_projection"),
        "gst_agent":             state.get("gst_analytics"),
        "tax_compliance_agent":  state.get("tax_compliance"),
        "related_party_agent":   state.get("related_party"),
        "industry_agent":        state.get("industry_intelligence"),
        "market_risk_agent":     state.get("market_risk"),
    }

    for name, section in section_map.items():
        if section is None:
            agents_failed.append(name)
            errors.append(f"{name}: returned None (unhandled exception in node)")
        elif section.status == AgentStatus.SKIPPED:
            agents_skipped.append(name)
        elif section.status == AgentStatus.ERROR:
            agents_failed.append(name)
        else:
            agents_executed.append(name)

    overview = state["input_data"].enriched_overview
    overall_status = (
        AgentStatus.ERROR if not agents_executed
        else AgentStatus.PARTIAL if agents_failed
        else AgentStatus.SUCCESS
    )

    insights = InsightsOutput(
        status=overall_status,
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        company_name=overview.company_name if overview else None,
        cin=overview.cin if overview else None,
        parsed_financials=state.get("parsed_financials"),
        ratio_report=state.get("ratio_report"),
        trend_report=state.get("trend_report"),
        banking_behaviour=state.get("banking_behaviour"),
        cash_flow_projection=state.get("cash_flow_projection"),
        gst_analytics=state.get("gst_analytics"),
        tax_compliance=state.get("tax_compliance"),
        related_party=state.get("related_party"),
        industry_intelligence=state.get("industry_intelligence"),
        market_risk=state.get("market_risk"),
        agents_executed=agents_executed,
        agents_skipped=agents_skipped,
        agents_failed=agents_failed,
        errors=errors,
    )

    return {"insights_output": insights}


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def _build_graph() -> StateGraph:
    """
    Build and compile the Agent 3 LangGraph StateGraph.

    Topology:
      START → parser → [ratio, trend, bank, gst, tax, rp, industry, market_risk]
                     → cash_flow → merge → END

    Wave 2 nodes fan out in parallel from parser via separate edges.
    A dedicated wave2_barrier node fans them back in before cash_flow.
    """
    graph = StateGraph(AgentState)

    # Register all nodes
    graph.add_node("parser", node_parser)
    graph.add_node("ratio", node_ratio)
    graph.add_node("trend", node_trend)
    graph.add_node("bank_statement", node_bank_statement)
    graph.add_node("gst", node_gst)
    graph.add_node("tax_compliance", node_tax_compliance)
    graph.add_node("related_party", node_related_party)
    graph.add_node("industry", node_industry)
    graph.add_node("market_risk", node_market_risk)
    graph.add_node("cash_flow", node_cash_flow)
    graph.add_node("merge", node_merge)

    # Wave 1: START → parser
    graph.set_entry_point("parser")

    # Wave 2 fan-out: parser → all Wave 2 nodes in parallel
    wave2_nodes = [
        "ratio", "trend", "bank_statement", "gst",
        "tax_compliance", "related_party", "industry", "market_risk",
    ]
    for node in wave2_nodes:
        graph.add_edge("parser", node)

    # Wave 3: all Wave 2 nodes → cash_flow (LangGraph waits for all incoming edges)
    for node in wave2_nodes:
        graph.add_edge(node, "cash_flow")

    # Final: cash_flow → merge → END
    graph.add_edge("cash_flow", "merge")
    graph.add_edge("merge", END)

    return graph.compile()


# Compiled graph — module-level singleton
_graph = _build_graph()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_analysis(input_data: Agent2Output, groq_api_key: str) -> InsightsOutput:
    """
    Run the full Agent 3 analysis pipeline.

    Args:
        input_data: Validated Agent 2 output.
        groq_api_key: Groq API key for LLM agents.

    Returns:
        InsightsOutput — merged results from all 10 sub-agents.
    """
    logger.info(
        "Agent 3 orchestrator starting — company: %s, CIN: %s",
        input_data.enriched_overview.company_name if input_data.enriched_overview else "unknown",
        input_data.enriched_overview.cin if input_data.enriched_overview else "unknown",
    )

    initial_state: AgentState = {
        "input_data": input_data,
        "groq_api_key": groq_api_key,
        "parsed_financials": None,
        "ratio_report": None,
        "trend_report": None,
        "banking_behaviour": None,
        "gst_analytics": None,
        "tax_compliance": None,
        "related_party": None,
        "industry_intelligence": None,
        "market_risk": None,
        "cash_flow_projection": None,
        "insights_output": None,
        "agents_executed": [],
        "agents_skipped": [],
        "agents_failed": [],
        "errors": [],
    }

    final_state = _graph.invoke(initial_state)

    insights: InsightsOutput = final_state.get("insights_output")
    if insights is None:
        logger.error("Orchestrator returned no insights_output — returning error payload.")
        insights = InsightsOutput(
            status=AgentStatus.ERROR,
            errors=["Orchestrator failed to produce output."],
        )

    logger.info(
        "Agent 3 orchestrator complete — executed: %s | skipped: %s | failed: %s",
        insights.agents_executed,
        insights.agents_skipped,
        insights.agents_failed,
    )
    return insights
