"""
core/state.py
LangGraph AgentState + all custom exceptions for the Web Scraper Agent.

AgentState is the single object that flows through every node.
Each node reads from it, does its work, and returns only the fields it changed.

Adding Agent 3:
    Read enriched_output from this state — no changes needed here.
"""

from __future__ import annotations

from typing import Any, Optional
from typing_extensions import TypedDict


# ── Custom exceptions ─────────────────────────────────────────────────────────

class WebScraperAgentError(Exception):
    """Base exception for all agent errors."""

class ScraperError(WebScraperAgentError):
    """Scraper failed after all retries."""

class RateLimitError(ScraperError):
    """Domain rate limit hit and unrecoverable."""

class SourceUnavailableError(ScraperError):
    """Source portal unreachable or returned non-200."""

class ParsingError(ScraperError):
    """HTML/JSON response could not be parsed into expected structure."""

class ValidationError(WebScraperAgentError):
    """Retrieved data failed confidence threshold or schema check."""

class ConflictingSourcesError(ValidationError):
    """Two sources returned contradictory values for the same field."""

class GapDetectionError(WebScraperAgentError):
    """Gap detection could not parse the Transformation Agent output."""

class SchemaError(WebScraperAgentError):
    """Enriched data could not be mapped to the BRD tab schema."""

class CacheError(WebScraperAgentError):
    """diskcache read/write failed."""


# ── AgentState ────────────────────────────────────────────────────────────────

class AgentState(TypedDict, total=False):
    """
    Shared state flowing through all LangGraph nodes.

    Population order:
      transformation_input  → set by main.py before graph runs
      missing_fields        → gap_detector
      scrape_tasks          → task_dispatcher
      raw_results           → scraper_executor
      retrieved_fields      → validator
      flagged_fields        → flag_engine
      enriched_output       → schema_normalizer + tab_writer
      errors                → appended by any node on non-fatal failure
      current_step          → updated by each node for debugging

    Agent 3 hook:
      analysis_output       → reserved; untouched by Agent 2
    """

    # Input from Agent 1
    transformation_input:   Any         # TransformationOutput

    # Gap detection
    missing_fields:         list        # list[MissingField]

    # Dispatch
    scrape_tasks:           list        # list[dict]

    # Raw scraper output — keyed by "_all_fields"
    raw_results:            dict

    # Post-validation
    retrieved_fields:       list        # list[RetrievedField]
    flagged_fields:         list        # list[FlaggedField]

    # Final enriched output
    enriched_output:        Any         # EnrichOutput

    # Agent 3 hook (untouched by Agent 2)
    analysis_output:        Optional[dict]

    # Pipeline metadata
    errors:                 list[str]
    current_step:           str
