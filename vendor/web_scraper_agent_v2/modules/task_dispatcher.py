"""
modules/task_dispatcher.py
LangGraph node: task_dispatcher

Maps each MissingField → ordered list of Sources to try.
Produces scrape_tasks — a list of task dicts consumed by the scraper executor.

Design rule:
  Each task specifies sources in priority order.
  Scraper executor tries them left-to-right; stops on first success.
  FLAG_MANUAL sentinel means "skip scraping, send straight to Flag Engine".
"""

from __future__ import annotations

import logging

from core.state import AgentState
from models.schemas import MissingField, Source

logger = logging.getLogger(__name__)

# ── Field → source routing table ─────────────────────────────────────────────
# List order = try order. First success wins.
# FLAG_MANUAL as sole source = immediately flag, never scrape.

FIELD_SOURCE_MAP: dict[MissingField, list[Source]] = {
    # Identity / registration
    MissingField.CIN:               [Source.MCA21, Source.ZAUBA],
    MissingField.PAN:               [Source.MCA21],
    MissingField.GSTIN:             [Source.GST_PORTAL],
    MissingField.INCORPORATION_DATE:[Source.MCA21, Source.ZAUBA],
    MissingField.DIRECTORS:         [Source.MCA21],
    MissingField.ADDRESS:           [Source.MCA21, Source.ZAUBA],
    MissingField.INDUSTRY:          [Source.MCA21],

    # Financial (computed from scraped financials — may partially fill)
    MissingField.EQUITY:            [Source.MCA21],         # from annual filing
    MissingField.LONG_TERM_DEBT:    [Source.MCA21],         # from charge filings
    MissingField.GROSS_PROFIT:      [Source.MCA21],
    MissingField.EBITDA:            [Source.MCA21],
    MissingField.EBIT:              [Source.MCA21],
    MissingField.OPERATING_EXPENSES:[Source.MCA21],
    MissingField.DEPRECIATION:      [Source.MCA21],
    MissingField.INTEREST_EXPENSE:  [Source.MCA21],
    MissingField.TAX:               [Source.MCA21],

    # Tab-level
    MissingField.CASH_FLOW_TAB:     [Source.MCA21],         # from ROC filing

    # MCA lookups (always attempted when CIN is known)
    MissingField.CHARGES:           [Source.MCA21],
    MissingField.LEGAL_CASES:       [Source.ECOURTS],

    # Always manual — FLAG_MANUAL sentinel, never scraped
    MissingField.BANK_STATEMENTS:       [Source.FLAG_MANUAL],
    MissingField.CIBIL_REPORT:          [Source.FLAG_MANUAL],
    MissingField.PROPERTY_TITLE_DEEDS:  [Source.FLAG_MANUAL],
    MissingField.VALUATION_REPORT:      [Source.FLAG_MANUAL],
    MissingField.LEGAL_OPINION_REPORT:  [Source.FLAG_MANUAL],
    MissingField.ID_PROOF_DIRECTORS:    [Source.FLAG_MANUAL],
}


def task_dispatcher(state: AgentState) -> dict:
    """
    LangGraph node function.
    Input  state fields: missing_fields
    Output state fields: scrape_tasks, current_step, errors (appended)

    Each task dict:
      {
        "field":    MissingField,
        "sources":  list[Source],   # ordered try list
        "priority": int,            # inherited from gap_detector ordering
      }
    """
    logger.info("=== Task Dispatcher: starting ===")
    errors: list[str] = list(state.get("errors", []))
    missing_fields: list[MissingField] = state.get("missing_fields", [])

    tasks: list[dict] = []

    for priority, field in enumerate(missing_fields, start=1):
        sources = FIELD_SOURCE_MAP.get(field)

        if sources is None:
            msg = f"task_dispatcher: no source mapping for field '{field.value}' — skipping"
            logger.warning(msg)
            errors.append(msg)
            continue

        task = {
            "field":    field,
            "sources":  sources,
            "priority": priority,
        }
        tasks.append(task)
        logger.debug(
            "Task queued: field=%s sources=%s",
            field.value,
            [s.value for s in sources],
        )

    manual_count  = sum(1 for t in tasks if t["sources"] == [Source.FLAG_MANUAL])
    scrape_count  = len(tasks) - manual_count

    logger.info(
        "Task Dispatcher: %d tasks total (%d to scrape, %d to flag manual)",
        len(tasks), scrape_count, manual_count,
    )

    return {
        "scrape_tasks":  tasks,
        "current_step":  "task_dispatcher",
        "errors":        errors,
    }
