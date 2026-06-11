"""
modules/flag_engine.py
LangGraph node: flag_engine

Two responsibilities:
  1. Immediately flag all ALWAYS_MANUAL_FIELDS (no scrape attempted)
  2. Pull any RetrievedField with flagged=True out of retrieved_fields
     and add it to flagged_fields for human review

Produces:
  flagged_fields  — FlaggedField objects for manual review queue
  retrieved_fields — cleaned list with only confident, unflagged results
"""

from __future__ import annotations

import logging

from config.settings import ALWAYS_MANUAL_FIELDS
from core.state import AgentState
from models.schemas import (
    FlaggedField,
    MissingField,
    RetrievedField,
    Source,
)

logger = logging.getLogger(__name__)

# Human-readable reasons for each always-manual field
_MANUAL_REASONS: dict[str, str] = {
    "bank_statements":      "Bank statements require direct submission — no free public source",
    "cibil_report":         "CIBIL is a paid bureau — must be obtained by the bank directly",
    "property_title_deeds": "Property documents require physical verification or state portal access",
    "valuation_report":     "Valuation requires a certified assessor — cannot be sourced online",
    "legal_opinion_report": "Legal opinion must be obtained from empanelled counsel",
    "id_proof_directors":   "Director ID proof is a physical KYC document — cannot be scraped",
}


def flag_engine(state: AgentState) -> dict:
    """
    LangGraph node function.
    Input  state fields: missing_fields, retrieved_fields
    Output state fields: flagged_fields, retrieved_fields (cleaned), current_step, errors
    """
    logger.info("=== Flag Engine: starting ===")
    errors:           list[str]           = list(state.get("errors", []))
    missing_fields:   list[MissingField]  = state.get("missing_fields", [])
    retrieved_fields: list[RetrievedField] = list(state.get("retrieved_fields", []))

    flagged: list[FlaggedField] = []

    # 1. Always-manual fields — flag immediately regardless of scrape result
    for field in missing_fields:
        if field.value in ALWAYS_MANUAL_FIELDS:
            reason = _MANUAL_REASONS.get(
                field.value,
                f"Field '{field.value}' requires manual collection",
            )
            flagged.append(FlaggedField(
                field_name=field,
                reason=reason,
                source=Source.FLAG_MANUAL,
            ))
            logger.debug("Flagged manual: %s — %s", field.value, reason)

    # 2. Low-confidence or conflicted RetrievedFields → move to flagged
    clean_retrieved:   list[RetrievedField] = []
    for rf in retrieved_fields:
        if rf.flagged:
            flagged.append(FlaggedField(
                field_name=rf.field_name,
                reason=rf.flag_reason or "Flagged by validator",
                source=rf.source,
            ))
            logger.warning(
                "Moving low-confidence field to manual queue: %s (%.2f) — %s",
                rf.field_name.value, rf.confidence, rf.flag_reason,
            )
        else:
            clean_retrieved.append(rf)

    logger.info(
        "Flag Engine: %d fields flagged manual, %d fields clean",
        len(flagged), len(clean_retrieved),
    )

    return {
        "flagged_fields":   flagged,
        "retrieved_fields": clean_retrieved,
        "current_step":     "flag_engine",
        "errors":           errors,
    }
