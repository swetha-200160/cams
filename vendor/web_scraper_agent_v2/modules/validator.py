"""
modules/validator.py
LangGraph node: validator

Receives raw scrape results (list[RetrievedField] per field),
applies cross-source validation, adjusts confidence scores,
and flags conflicts for manual review.

Rules:
  - Single source           → confidence unchanged (base score from scraper)
  - Two sources agree       → confidence += CROSS_VALIDATE_BOOST (capped at 1.0)
  - Two sources disagree    → confidence = CONFLICT_SCORE, flagged=True
  - Duplicate fields        → keep highest-confidence, log the rest
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from config.settings import CONFLICT_SCORE, CROSS_VALIDATE_BOOST
from core.state import AgentState
from models.schemas import MissingField, RetrievedField

logger = logging.getLogger(__name__)


def _values_agree(a: Any, b: Any) -> bool:
    """
    Loose equality check for cross-source validation.
    Normalises strings (upper, strip) before comparing.
    Lists compared as sets.
    """
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().upper() == b.strip().upper()
    if isinstance(a, list) and isinstance(b, list):
        return set(str(x).upper() for x in a) == set(str(x).upper() for x in b)
    return str(a).strip().upper() == str(b).strip().upper()


def _cross_validate(fields: list[RetrievedField]) -> list[RetrievedField]:
    """
    Group by field_name, apply cross-source rules, return validated list.
    """
    grouped: dict[MissingField, list[RetrievedField]] = defaultdict(list)
    for f in fields:
        grouped[f.field_name].append(f)

    validated: list[RetrievedField] = []

    for field_name, group in grouped.items():
        if len(group) == 1:
            # Single source — pass through unchanged
            validated.append(group[0])
            continue

        # Multiple sources — check agreement
        primary = max(group, key=lambda f: f.confidence)
        others  = [f for f in group if f is not primary]

        all_agree = all(_values_agree(primary.value, other.value) for other in others)

        if all_agree:
            # Boost confidence for cross-validated agreement
            new_conf = min(1.0, primary.confidence + CROSS_VALIDATE_BOOST)
            validated.append(primary.model_copy(update={
                "confidence":      new_conf,
                "cross_validated": True,
            }))
            logger.debug(
                "Cross-validated %s: %.2f → %.2f (%d sources agree)",
                field_name.value, primary.confidence, new_conf, len(group),
            )
        else:
            # Conflict — flag for manual review
            conflict_note = (
                f"Conflict: {primary.source.value}={primary.value!r} vs "
                + ", ".join(f"{o.source.value}={o.value!r}" for o in others)
            )
            logger.warning("Source conflict for %s: %s", field_name.value, conflict_note)
            validated.append(primary.model_copy(update={
                "confidence":  CONFLICT_SCORE,
                "flagged":     True,
                "flag_reason": conflict_note,
            }))

    return validated


def validator(state: AgentState) -> dict:
    """
    LangGraph node function.
    Input  state fields: raw_results (list[RetrievedField] collected by executor)
    Output state fields: retrieved_fields, current_step, errors (appended)
    """
    logger.info("=== Validator: starting ===")
    errors: list[str] = list(state.get("errors", []))

    raw_fields: list[RetrievedField] = state.get("raw_results", {}).get(
        "_all_fields", []
    )

    if not raw_fields:
        logger.warning("Validator: no raw fields to validate")
        return {
            "retrieved_fields": [],
            "current_step":     "validator",
            "errors":           errors,
        }

    try:
        validated = _cross_validate(raw_fields)

        flagged_count = sum(1 for f in validated if f.flagged)
        logger.info(
            "Validator: %d fields validated (%d flagged)",
            len(validated), flagged_count,
        )

        return {
            "retrieved_fields": validated,
            "current_step":     "validator",
            "errors":           errors,
        }

    except Exception as exc:
        msg = f"validator unexpected error: {exc}"
        logger.exception(msg)
        errors.append(msg)
        return {
            "retrieved_fields": [],
            "current_step":     "validator",
            "errors":           errors,
        }
