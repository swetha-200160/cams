"""
db/tab_writer.py
LangGraph node: tab_writer

Final node of the Web Scraper Agent.
  1. Serialises EnrichOutput → enrich_output.json
  2. Prints a clean terminal summary
  3. Passes state unchanged to the next agent (Analysis Agent stub)

Why JSON and not a database:
  Mirrors Agent 1's pattern (transformation_output.json).
  Agent 3 reads the file — no DB coupling needed at this stage.
  Swap to DB insert here later without touching any other module.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config.settings import ENRICHED_OUTPUT_FILE, OUTPUT_DIR
from core.state import AgentState
from models.schemas import EnrichOutput

logger = logging.getLogger(__name__)


def _serialise(obj: object) -> object:
    """JSON serialiser for Pydantic models and datetimes."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serialisable")


def _print_summary(output: EnrichOutput) -> None:
    s = output.summary
    sep = "─" * 56
    print(f"\n{sep}")
    print(f"  Web Scraper Agent — Run Complete")
    print(sep)
    print(f"  Status          : {output.status.upper()}")
    print(f"  Fields detected : {s.fields_detected}")
    print(f"  Fields scraped  : {s.fields_scraped}")
    print(f"  Flagged manual  : {s.fields_flagged}")
    print(f"  Failed          : {s.fields_failed}")
    print(f"  Sources used    : {', '.join(s.sources_used) or 'none'}")

    if output.flagged_manual:
        print(f"\n  Manual review required:")
        for f in output.flagged_manual:
            print(f"    • {f.field_name.value:<30} {f.reason}")

    if s.errors:
        print(f"\n  Non-fatal errors ({len(s.errors)}):")
        for e in s.errors[-5:]:          # show last 5 only
            print(f"    ⚠  {e}")

    print(f"\n  Output → {ENRICHED_OUTPUT_FILE}")
    print(f"{sep}\n")


def tab_writer(state: AgentState) -> dict:
    """
    LangGraph node function.
    Input  state fields: enriched_output
    Output state fields: current_step, errors (appended)
    State is otherwise passed through unchanged to Agent 3.
    """
    logger.info("=== Tab Writer: starting ===")
    errors: list[str] = list(state.get("errors", []))

    enriched_output: EnrichOutput = state.get("enriched_output")  # type: ignore[assignment]
    if enriched_output is None:
        msg = "tab_writer: enriched_output missing from state — nothing to write"
        logger.error(msg)
        errors.append(msg)
        return {"current_step": "tab_writer", "errors": errors}

    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        payload = enriched_output.model_dump()
        with open(ENRICHED_OUTPUT_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=_serialise)

        logger.info("enrich_output.json written to %s", ENRICHED_OUTPUT_FILE)

    except Exception as exc:
        msg = f"tab_writer: failed to write output file: {exc}"
        logger.exception(msg)
        errors.append(msg)

    _print_summary(enriched_output)

    return {
        "current_step": "tab_writer",
        "errors":       errors,
    }
