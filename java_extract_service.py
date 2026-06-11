"""
java_extract_service.py

Extracts field values from documents using Gemini.
Called by the Java-facing endpoint  POST /api/java/extract.

No DB interaction. No file I/O. Pure: receive → decode → Gemini → return.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_MODEL = "gemini-2.5-flash"

_MIME_MAP: Dict[str, str] = {
    "pdf":  "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc":  "application/msword",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls":  "application/vnd.ms-excel",
    "png":  "image/png",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "tiff": "image/tiff",
    "bmp":  "image/bmp",
}


def _build_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in the environment")
    return genai.Client(api_key=api_key)


def _extract_from_document(
    client: genai.Client,
    file_bytes: bytes,
    mime_type: str,
    keys: List[str],
) -> Dict[str, Optional[str]]:
    """
    Send one document to Gemini and ask it to extract the requested field values.
    Returns a dict of {field_name: value_or_None}.
    """
    prompt = (
        "You are a document field extractor.\n"
        f"Extract the values for ONLY these fields: {json.dumps(keys)}\n"
        "Return ONLY a valid JSON object, nothing else.\n"
        f"Example format: {json.dumps({k: 'extracted value' for k in keys})}\n"
        "If a field is not found in the document, set its value to null.\n"
        "Do not include markdown, code fences, or any explanation."
    )

    response = client.models.generate_content(
        model=_MODEL,
        contents=[
            types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
            types.Part.from_text(text=prompt),
        ],
        config=types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=2048,
        ),
    )

    if not response.text:
        raise ValueError("Gemini returned an empty or blocked response")

    raw = response.text.strip()

    # Strip markdown code fences if Gemini wraps the JSON despite instructions
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw)


def _post_to_webhook(cam_id: str, details: Dict[str, Optional[str]]) -> bool:
    """
    POST extracted field values to the Java backend webhook.
    Payload format: [{"fieldName": "...", "fieldValue": "..."}, ...]
    Null values are excluded from the payload.
    Returns True on success, False on failure.
    """
    base_url = os.environ.get("JAVA_WEBHOOK_BASE_URL", "").rstrip("/")
    if not base_url:
        raise RuntimeError("JAVA_WEBHOOK_BASE_URL is not set in the environment")

    url = f"{base_url}/cams/webhook/{cam_id}"
    payload = [
        {"fieldName": k, "fieldValue": v}
        for k, v in details.items()
        if v is not None
    ]

    try:
        response = httpx.post(url, json=payload, timeout=30)
        response.raise_for_status()
        logger.info("Webhook delivered to %s — HTTP %s", url, response.status_code)
        return True
    except Exception as exc:
        logger.error("Webhook POST to %s failed: %s", url, exc)
        return False


def extract_fields(
    cam_id: str,
    documents: List[Dict[str, Any]],
    keys: List[str],
) -> Dict[str, Any]:
    """
    Main entry point called by the router.

    For each document:
      - decode base64 → bytes
      - call Gemini with the bytes + keys
      - merge results (first non-null value for each key wins)

    Returns:
      {
        "camId": "...",
        "details": {"Customer Name": "...", "CAM Date": "..."}
      }
    """
    client = _build_client()

    # Start with all keys unmapped
    merged: Dict[str, Optional[str]] = {k: None for k in keys}

    for doc in documents:
        file_type = (doc.get("fileType") or "").lower().lstrip(".")
        mime_type = _MIME_MAP.get(file_type)

        if not mime_type:
            logger.warning(
                "Unsupported fileType '%s' for file '%s' — skipping",
                file_type, doc.get("fileName"),
            )
            continue

        try:
            file_bytes = base64.b64decode(doc["base64"])
        except Exception as exc:
            logger.warning(
                "base64 decode failed for '%s': %s — skipping",
                doc.get("fileName"), exc,
            )
            continue

        try:
            result = _extract_from_document(client, file_bytes, mime_type, keys)
        except Exception as exc:
            logger.warning(
                "Gemini extraction failed for '%s': %s — skipping",
                doc.get("fileName"), exc,
            )
            continue

        # Fill in keys that are still None from this document's result
        for k in keys:
            if merged[k] is None and result.get(k) is not None:
                merged[k] = result[k]

        # All keys found — no need to process remaining documents
        if all(v is not None for v in merged.values()):
            break

    webhook_ok = _post_to_webhook(cam_id, merged)

    return {
        "camId": cam_id,
        "status": "success",
        "webhook": "delivered" if webhook_ok else "failed",
        "details": merged,
    }
