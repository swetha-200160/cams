"""
core/graph.py
LangGraph StateGraph for the Web Scraper Agent.

Node execution order:
  gap_detector → task_dispatcher → scraper_executor → validator
  → flag_engine → schema_normalizer → tab_writer → analysis_stub → END

Adding Agent 3 — change only the 3 lines marked AGENT_3_SWAP:
  1. Import your analysis agent graph
  2. Replace _analysis_stub with your node function
  3. Nothing else changes
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from core.state import AgentState
from modules.gap_detector import gap_detector
from modules.task_dispatcher import task_dispatcher
from modules.scraper_executor import scraper_executor
from modules.validator import validator
from modules.flag_engine import flag_engine
from modules.schema_normalizer import schema_normalizer
from modules.tab_writer import tab_writer

logger = logging.getLogger(__name__)


# ── Agent 3 stub ──────────────────────────────────────────────────────────────
# AGENT_3_SWAP: replace this with your real analysis agent node

def _analysis_stub(state: AgentState) -> dict:
    """
    Placeholder for Agent 3 — Analysis Agent.
    Logs that it was called and passes state through unchanged.
    """
    logger.info(
        "analysis_stub called — Agent 3 not yet connected. "
        "enriched_output is ready in state."
    )
    return {}


# ── Graph assembly ────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """Compile and return the Web Scraper Agent LangGraph."""
    graph = StateGraph(AgentState)

    graph.add_node("gap_detector",      gap_detector)
    graph.add_node("task_dispatcher",   task_dispatcher)
    graph.add_node("scraper_executor",  scraper_executor)
    graph.add_node("validator",         validator)
    graph.add_node("flag_engine",       flag_engine)
    graph.add_node("schema_normalizer", schema_normalizer)
    graph.add_node("tab_writer",        tab_writer)
    graph.add_node("analysis_stub",     _analysis_stub)   # AGENT_3_SWAP

    graph.add_edge(START,               "gap_detector")
    graph.add_edge("gap_detector",      "task_dispatcher")
    graph.add_edge("task_dispatcher",   "scraper_executor")
    graph.add_edge("scraper_executor",  "validator")
    graph.add_edge("validator",         "flag_engine")
    graph.add_edge("flag_engine",       "schema_normalizer")
    graph.add_edge("schema_normalizer", "tab_writer")
    graph.add_edge("tab_writer",        "analysis_stub")   # AGENT_3_SWAP
    graph.add_edge("analysis_stub",     END)               # AGENT_3_SWAP

    compiled = graph.compile()
    logger.debug("Graph compiled successfully")
    return compiled
