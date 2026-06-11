from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Type

from pydantic import BaseModel

from agent1_patch_source.config import settings
from agent1_patch_source.llmextract import extract_json_with_llm
from agent1_patch_source.textextractors import extract_text

logger = logging.getLogger(__name__)

# Explicit MIME type map for known document extensions.
# Always used in preference to mimetypes.guess_type() because on Windows
# the system registry may return "application/octet-stream", "application/zip",
# or other incorrect values for .docx / .xlsx files, preventing correct routing.
_SUFFIX_MIME: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls":  "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv":  "text/csv",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp":  "image/bmp",
    ".tif":  "image/tiff",
    ".tiff": "image/tiff",
}


@dataclass
class PipelineResult:
    data: BaseModel
    meta: dict[str, Any]


def _guess_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    # Use explicit mapping first — avoids Windows registry returning wrong types
    if suffix in _SUFFIX_MIME:
        return _SUFFIX_MIME[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


async def run_multi_document_pipeline(*, file_paths: list[str], model_cls: Type[BaseModel], doc_hint: str) -> PipelineResult:
    text_parts: list[str] = []
    sources: list[dict[str, Any]] = []

    for file_path in file_paths:
        path = Path(file_path)
        try:
            mime_type = _guess_mime_type(path)
            result = extract_text(
                path.read_bytes(),
                mime_type=mime_type,
                filename=path.name,
                max_pages=settings.max_pages_pdf,
                ocr_lang=settings.ocr_lang,
            )
            sources.append({
                "filename": path.name,
                "source": result.source,
                "pages_used": result.pages_used,
                "chars": len(result.text or ""),
            })
            if result.text:
                text_parts.append(
                    f"FILE: {path.name}\nSOURCE: {result.source}\nTEXT START\n{result.text}\nTEXT END"
                )
        except Exception as exc:
            logger.warning("Failed to extract text from '%s' (mime=%s): %s", path.name, _guess_mime_type(path), exc)
            sources.append({
                "filename": path.name,
                "source": "error",
                "pages_used": 0,
                "chars": 0,
            })

    combined_text = "\n\n".join(text_parts).strip()
    data = await extract_json_with_llm(document_text=combined_text, model_cls=model_cls, doc_hint=doc_hint)
    return PipelineResult(
        data=data,
        meta={
            "mode": "multi_document",
            "doc_hint": doc_hint,
            "files_used": len(file_paths),
            "chars_sent": len(combined_text),
            "sources": sources,
        },
    )
