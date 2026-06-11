"""
java_extract_router.py

FastAPI router for the Java-facing field extraction endpoint.
Mounted at:  POST /api/java/extract

Receives documents (base64) + field keys from the Java backend,
runs Gemini extraction, and returns the values as JSON.
Does not touch any database.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from java_extract_service import extract_fields

router = APIRouter(prefix="/api/java", tags=["Java Extract"])


# ── Request / Response models ─────────────────────────────────────────────────

class CamDocument(BaseModel):
    documentName: str
    fileName: str
    fileType: str
    base64: str


class ExtractRequest(BaseModel):
    camId: str
    documents: List[CamDocument]
    keys: List[str]


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post(
    "/extract",
    summary="Extract field values from documents",
    description="""
Receives a list of documents (base64-encoded) and a list of field names.
Uses Gemini to extract the value of each field and returns the results.

**Supported file types:** PDF, DOCX, DOC, XLSX, XLS, PNG, JPG, JPEG, TIFF, BMP.

### Request body
| Field | Type | Description |
|---|---|---|
| `camId` | string | CAM record identifier — passed through to the response |
| `documents` | array | Each item has `documentName`, `fileName`, `fileType`, `base64` |
| `keys` | array | Field names to extract, e.g. `["Customer Name", "CAM Date"]` |

### Response
```json
{
  "camId": "6a02d1d6fdd2d12d04cf77e4",
  "details": {
    "Customer Name": "Oricon Enterprises Limited",
    "CAM Date": "15-Mar-2024"
  }
}
```
Fields not found in any document are returned as `null`.

### Errors
| HTTP | Reason |
|---|---|
| `400` | `documents` or `keys` list is empty |
| `500` | Gemini API key missing or extraction failure |
""",
)
def extract(payload: ExtractRequest) -> Dict[str, Any]:
    if not payload.documents:
        raise HTTPException(status_code=400, detail="'documents' list must not be empty")
    if not payload.keys:
        raise HTTPException(status_code=400, detail="'keys' list must not be empty")

    try:
        return extract_fields(
            cam_id=payload.camId,
            documents=[doc.model_dump() for doc in payload.documents],
            keys=payload.keys,
        )
    except RuntimeError as exc:
        # e.g. GEMINI_API_KEY not set
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {exc}") from exc
