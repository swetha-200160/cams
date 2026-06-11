# Java Extract Endpoint

**Extracts field values from documents using Gemini AI.**
**No database interaction — pure receive → extract → return.**

---

## Endpoint

```
POST /api/java/extract
Content-Type: application/json
```

---

## Request Body

```json
{
  "camId": "6a02d1d6fdd2d12d04cf77e4",
  "documents": [
    {
      "documentName": "Balance Sheet",
      "fileName": "balance_sheet.pdf",
      "fileType": "pdf",
      "base64": "<base64-encoded file bytes>"
    }
  ],
  "keys": [
    "Customer Name",
    "CAM Date"
  ]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `camId` | string | Yes | CAM record identifier — passed through unchanged to the response |
| `documents` | array | Yes | List of documents to extract from |
| `documents[].documentName` | string | Yes | Human-readable document label |
| `documents[].fileName` | string | Yes | Original file name (e.g. `report.pdf`) |
| `documents[].fileType` | string | Yes | File extension without dot (e.g. `pdf`, `docx`, `png`) |
| `documents[].base64` | string | Yes | Base64-encoded file bytes |
| `keys` | array | Yes | Field names to extract (e.g. `["Customer Name", "CAM Date"]`) |

---

## Supported File Types

| Extension | MIME Type |
|---|---|
| `pdf` | application/pdf |
| `docx` | application/vnd.openxmlformats-officedocument.wordprocessingml.document |
| `doc` | application/msword |
| `xlsx` | application/vnd.openxmlformats-officedocument.spreadsheetml.sheet |
| `xls` | application/vnd.ms-excel |
| `png` | image/png |
| `jpg` / `jpeg` | image/jpeg |
| `tiff` | image/tiff |
| `bmp` | image/bmp |

Unsupported file types are skipped with a warning log — they do not cause the request to fail.

---

## What It Does (Step by Step)

```
1. Receive (camId + documents[] + keys[])
         |
         v
2. For each document:
   a. Decode base64  →  raw file bytes (in memory, no disk write)
   b. Detect MIME type from fileType
   c. Send bytes + keys to Gemini (gemini-2.5-flash)
      Prompt: "Extract ONLY these fields. Return JSON only."
         |
         v
3. Merge results across all documents
   - First non-null value per key wins
   - Stops early if all keys are found
         |
         v
4. Return JSON response
```

---

## Success Response

```json
{
  "camId": "6a02d1d6fdd2d12d04cf77e4",
  "details": {
    "Customer Name": "Oricon Enterprises Limited",
    "CAM Date": "15-Mar-2024"
  }
}
```

Fields not found in any document are returned as `null`:

```json
{
  "camId": "6a02d1d6fdd2d12d04cf77e4",
  "details": {
    "Customer Name": "Oricon Enterprises Limited",
    "CAM Date": null
  }
}
```

---

## Error Responses

| HTTP | Reason |
|---|---|
| `400` | `documents` list is empty |
| `400` | `keys` list is empty |
| `500` | `GEMINI_API_KEY` not set in `.env` |
| `500` | Gemini API call failed |

---

## Configuration

| `.env` Key | Description |
|---|---|
| `GEMINI_API_KEY` | Google Gemini API key — already used by the rest of the service |

No new environment variables are required.

---

## Files Involved

| File | Role |
|---|---|
| `java_extract_service.py` | Core logic — base64 decode, Gemini call, result merge |
| `java_extract_router.py` | FastAPI route definition + Pydantic request/response models |
| `main.py` | Registers the router (2-line change only) |

**No changes to `db_client.py`, any PostgreSQL table, or any MongoDB collection.**

---

## Notes

- Processing time depends on document size and number of documents. The endpoint is synchronous — it responds only when extraction is complete.
- If multiple documents contain a value for the same key, the value from the **first document** that returns it is used.
- Scanned PDFs, images, and complex table layouts are handled natively by Gemini — no separate OCR setup required.
