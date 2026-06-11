"""
modules/schema_normalizer.py
LangGraph node: schema_normalizer

Takes validated RetrievedField objects and merges them into the
Transformation Agent's tab_data, producing an enriched TabData object.

Design rule:
  Never overwrite a value that already exists from Agent 1 documents.
  Retrieved data fills GAPS only — it does not replace document-derived data.
  Source is always traceable via retrieved_fields audit trail.
"""

from __future__ import annotations

import logging
from typing import Any

from core.state import AgentState
from models.schemas import OverviewTab, TabData
from models.schemas import EnrichOutput, EnrichmentSummary
from models.schemas import MissingField, RetrievedField

logger = logging.getLogger(__name__)


def _apply_to_overview(
    overview:   OverviewTab,
    field_name: MissingField,
    value:      Any,
) -> OverviewTab:
    """
    Return a new OverviewTab with the retrieved value applied.
    Only fills if the existing value is None.
    """
    overview_field_map: dict[MissingField, str] = {
        MissingField.CIN:               "cin",
        MissingField.PAN:               "pan",
        MissingField.GSTIN:             "gstin",
        MissingField.ADDRESS:           "address",
        MissingField.DIRECTORS:         "directors",
        MissingField.INCORPORATION_DATE:"incorporation_date",
        MissingField.INDUSTRY:          "industry",
    }

    attr = overview_field_map.get(field_name)
    if not attr:
        return overview  # field doesn't map to overview tab

    current = getattr(overview, attr, None)
    if current is not None:
        logger.debug(
            "Schema normalizer: skipping %s — already has value '%s'",
            attr, current,
        )
        return overview

    updated = overview.model_copy(update={attr: value})
    logger.debug("Schema normalizer: filled overview.%s = %r", attr, value)
    return updated


def schema_normalizer(state: AgentState) -> dict:
    """
    LangGraph node function.
    Input  state fields: transformation_input, retrieved_fields, flagged_fields
    Output state fields: enriched_output, current_step, errors (appended)
    """
    logger.info("=== Schema Normalizer: starting ===")
    errors:           list[str]            = list(state.get("errors", []))
    retrieved_fields: list[RetrievedField] = state.get("retrieved_fields", [])
    flagged_fields                         = state.get("flagged_fields", [])

    tx      = state["transformation_input"]
    tab_data: TabData = tx.tab_data.model_copy(deep=True)

    fields_applied = 0
    sources_used:  set[str] = set()

    for rf in retrieved_fields:
        try:
            tab_data.overview = _apply_to_overview(
                tab_data.overview, rf.field_name, rf.value
            )
            fields_applied += 1
            sources_used.add(rf.source.value)
        except Exception as exc:
            msg = f"schema_normalizer: failed to apply {rf.field_name.value}: {exc}"
            logger.warning(msg)
            errors.append(msg)

    # Determine enrichment status
    total_missing  = len(state.get("missing_fields", []))
    total_flagged  = len(flagged_fields)
    total_failed   = total_missing - fields_applied - total_flagged

    if fields_applied > 0 or total_flagged > 0:
        status = "partial_success" if total_failed > 0 else "success"
    else:
        status = "failed"

    summary = EnrichmentSummary(
        fields_detected=total_missing,
        fields_scraped=fields_applied,
        fields_flagged=total_flagged,
        fields_failed=max(0, total_failed),
        sources_used=sorted(sources_used),
        errors=errors,
    )

    raw_scraped_data = dict(getattr(tx, "auxiliary_data", {}) or {})
    raw_scraped_data.update(state.get("raw_results", {}))

    enriched_output = EnrichOutput(
        status=status,
        enriched_tabs=tab_data,
        retrieved_fields=retrieved_fields,
        flagged_manual=flagged_fields,
        raw_scraped_data=raw_scraped_data,
        summary=summary,
    )

    logger.info(
        "Schema Normalizer: status=%s | scraped=%d | flagged=%d | failed=%d",
        status, fields_applied, total_flagged, max(0, total_failed),
    )

    return {
        "enriched_output": enriched_output,
        "current_step":    "schema_normalizer",
        "errors":          errors,
    }
