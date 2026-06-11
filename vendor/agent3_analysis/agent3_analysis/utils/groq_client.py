"""
utils/groq_client.py
Groq LLM wrapper with structured JSON response support and retry logic.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, Optional

from groq import Groq

from agent3_analysis.config import (
    GROQ_MAX_TOKENS,
    GROQ_MODEL,
    GROQ_RETRY_ATTEMPTS,
    GROQ_RETRY_DELAY_SECONDS,
    GROQ_TEMPERATURE,
)

logger = logging.getLogger(__name__)


def call_groq(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    model: str = GROQ_MODEL,
    max_tokens: int = GROQ_MAX_TOKENS,
    temperature: float = GROQ_TEMPERATURE,
) -> str:
    """
    Call the Groq LLM and return raw response text.

    Args:
        api_key: Groq API key.
        system_prompt: System instruction for the model.
        user_prompt: User message / data payload.
        model: Groq model identifier.
        max_tokens: Max tokens in response.
        temperature: Sampling temperature (0.0 = deterministic).

    Returns:
        Raw response string from the model.

    Raises:
        RuntimeError: If all retry attempts are exhausted.
    """
    client = Groq(api_key=api_key)

    for attempt in range(1, GROQ_RETRY_ATTEMPTS + 1):
        try:
            logger.debug("Groq call attempt %d/%d — model=%s", attempt, GROQ_RETRY_ATTEMPTS, model)
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content

        except Exception as exc:
            logger.warning("Groq call attempt %d failed: %s", attempt, exc)
            if attempt < GROQ_RETRY_ATTEMPTS:
                time.sleep(GROQ_RETRY_DELAY_SECONDS)

    raise RuntimeError(f"Groq call failed after {GROQ_RETRY_ATTEMPTS} attempts.")


def call_groq_json(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    model: str = GROQ_MODEL,
    max_tokens: int = GROQ_MAX_TOKENS,
) -> Dict[str, Any]:
    """
    Call Groq and parse the response as JSON.
    Strips markdown code fences if the model wraps output.

    Args:
        api_key: Groq API key.
        system_prompt: Must instruct model to return ONLY valid JSON.
        user_prompt: User message / data payload.
        model: Groq model identifier.
        max_tokens: Max tokens in response.

    Returns:
        Parsed JSON dictionary.

    Raises:
        ValueError: If response cannot be parsed as JSON.
        RuntimeError: If all retry attempts are exhausted.
    """
    raw = call_groq(api_key, system_prompt, user_prompt, model, max_tokens)
    return _parse_json(raw)


def _parse_json(raw: str) -> Dict[str, Any]:
    """
    Safely parse a JSON object from LLM output.
    Handles markdown code fences and leading/trailing text.

    Args:
        raw: Raw LLM response string.

    Returns:
        Parsed dictionary.

    Raises:
        ValueError: If no valid JSON object found.
    """
    # Strip markdown fences
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fallback: extract first {...} block
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        logger.error("No JSON object found in LLM response: %s", raw[:200])
        raise ValueError("LLM did not return a valid JSON object.")

    try:
        return json.loads(match.group())
    except json.JSONDecodeError as exc:
        logger.error("JSON decode error: %s | raw: %s", exc, raw[:200])
        raise ValueError(f"Failed to parse LLM JSON response: {exc}") from exc
