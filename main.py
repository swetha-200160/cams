
from __future__ import annotations

from typing import List

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse

from models import (
    ApplicationCreateRequest,
    DraftUpdateRequest,
    PipelineFromDbRequest,
    PipelineRunRequest,
)
from orchestrator_service import UnifiedOrchestratorService
from settings import settings
from java_extract_router import router as java_extract_router
import pdf_segmentation_service

_DESCRIPTION = """
## CAMS — Credit Assessment & Memo System

Automates the end-to-end generation of **Credit Assessment Memos (CAMs)** from raw borrower documents.

---

### Typical Workflow

Follow these steps in order to produce a CAM for a borrower:

**Step 1 — Create an Application**
`POST /api/applications`
Register a new loan application. Supply the company name and optional metadata.
The response returns the `application_id` you will use in every subsequent call.

**Step 2 — Upload Documents**
`POST /api/applications/{application_id}/documents`
Upload the borrower's source files (PDF, DOCX, XLSX, images).
You can also use the interactive upload form at [/upload](/upload).

**Step 3 — Run the Pipeline**
`POST /api/orchestrator/run`
Kick off the four-stage analysis pipeline:
1. **Transformation** — extracts raw data from every document
2. **Enrichment** — normalises and cross-references the extracted data
3. **Analysis** — produces financial ratios, risk flags, and insights
4. **CAM Generation** — assembles the final memo draft

The endpoint returns immediately (`202 Accepted`); the pipeline runs in the background.

**Step 4 — Poll for Status**
`GET /api/orchestrator/{application_id}/status`
Check which stage is running and whether the pipeline has finished.
Wait until `status` is `"success"` or `"partial_success"` before proceeding.

**Step 5 — Review Outputs**
| Endpoint | What it returns |
|---|---|
| `GET /api/orchestrator/{application_id}/draft` | Structured CAM draft (sections, blocks, citations) |
| `GET /api/orchestrator/{application_id}/tabs` | All analytical tabs in one bundle |
| `GET /api/orchestrator/{application_id}/insights` | Key insights / risk flags |

**Step 6 — Edit the Draft** *(optional)*
`PATCH /api/orchestrator/{application_id}/draft/block`
Overwrite a specific text block inside the draft before exporting.

**Step 7 — Export**
| Endpoint | Format |
|---|---|
| `GET /api/orchestrator/{application_id}/export/docx` | Word (.docx) |
| `GET /api/orchestrator/{application_id}/export/pdf` | PDF |

---

### Notes
- All `application_id` values are returned by `POST /api/applications` and follow the pattern `<slug>-<8-hex-chars>` (e.g. `abc-enterprises-pvt-ltd-656b8b83`).
- The pipeline runs asynchronously. Poll `/status` until completion before calling draft or export endpoints.
- Setting `use_ai_writer: true` in the run request enables Gemini-powered rich content generation (requires `GEMINI_API_KEY` in `.env`).
"""

_TAGS = [
    {
        "name": "Applications",
        "description": "Create loan applications and upload borrower source documents.",
    },
    {
        "name": "Pipeline",
        "description": "Trigger the analysis pipeline and monitor its progress.",
    },
    {
        "name": "Results",
        "description": "Retrieve the generated CAM draft, tabs bundle, and insights.",
    },
    {
        "name": "Export",
        "description": "Download the finished CAM as a Word or PDF file.",
    },
    {
        "name": "System",
        "description": "Health check and service metadata.",
    },
    {
        "name": "Documents",
        "description": "PDF utilities — segment a PDF into individual pages and download as a ZIP.",
    },
]

app = FastAPI(
    title=settings.title,
    version=settings.version,
    description=_DESCRIPTION,
    openapi_tags=_TAGS,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

service = UnifiedOrchestratorService()
app.include_router(java_extract_router)


@app.get(
    "/health",
    tags=["System"],
    summary="Health check",
    description="Returns `ok` if the service is running. Use this to verify the server is reachable before making other calls.",
)
def health():
    return {"status": "ok", "service": settings.title, "version": settings.version}


@app.get(
    "/",
    tags=["System"],
    summary="Service info",
    description="Returns basic service metadata and links to the API documentation.",
)
def root():
    return {
        "service": settings.title,
        "version": settings.version,
        "mode": "backend-only",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get(
    "/api/applications",
    tags=["Applications"],
    summary="List all loan applications",
    description="Returns all applications ordered by most recently updated. Merges DB records (bypass flow) with filesystem records (normal flow).",
)
def list_applications():
    from db_client import list_db_applications
    return list_db_applications()


@app.post(
    "/api/applications",
    tags=["Applications"],
    summary="Create a new loan application",
    description="""
Register a new borrower application.

- **company_name** *(required)* — legal name of the borrower entity.
- **application_id** — leave blank to have the system generate one automatically (recommended). Format: `<slug>-<8-hex-chars>`.
- **loan_amount** — requested loan amount in the base currency.
- **loan_type** — e.g. `term_loan`, `working_capital`, `overdraft`.
- **industry** — sector classification for the borrower.
- **application_date** — ISO date (`YYYY-MM-DD`); defaults to today when omitted.

The `application_id` returned here is required for all subsequent API calls.
""",
)
def create_application(payload: ApplicationCreateRequest):
    try:
        return service.create_application(payload)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get(
    "/upload",
    response_class=HTMLResponse,
    tags=["Applications"],
    summary="Interactive document upload form",
    description="Opens a browser-based HTML form for uploading borrower documents. Useful for quick manual testing without a REST client.",
    include_in_schema=True,
)
def upload_form():
    return HTMLResponse(content="""
<!DOCTYPE html>
<html>
<head>
    <title>CAMS — Upload Documents</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 600px; margin: 60px auto; padding: 0 20px; }
        h2 { color: #333; }
        label { display: block; margin-bottom: 6px; font-weight: bold; }
        input[type=text] { width: 100%; padding: 8px; margin-bottom: 16px; box-sizing: border-box; border: 1px solid #ccc; border-radius: 4px; }
        input[type=file] { margin-bottom: 16px; }
        button { background: #007bff; color: white; padding: 10px 24px; border: none; border-radius: 4px; cursor: pointer; font-size: 15px; }
        button:hover { background: #0056b3; }
        #result { margin-top: 20px; padding: 12px; background: #f4f4f4; border-radius: 4px; white-space: pre-wrap; display: none; }
    </style>
</head>
<body>
    <h2>CAMS — Upload Borrower Documents</h2>
    <label>Application ID</label>
    <input type="text" id="appId" placeholder="e.g. abc-enterprises-pvt-ltd-656b8b83" />
    <label>Select Files (PDF, DOCX, XLSX, images)</label>
    <input type="file" id="fileInput" multiple accept=".pdf,.docx,.doc,.xlsx,.xls,.csv,.png,.jpg,.jpeg,.tiff,.bmp" />
    <br/>
    <button onclick="uploadFiles()">Upload</button>
    <div id="result"></div>
    <script>
        async function uploadFiles() {
            const appId = document.getElementById('appId').value.trim();
            const files = document.getElementById('fileInput').files;
            if (!appId) { alert('Enter Application ID'); return; }
            if (files.length === 0) { alert('Select at least one file'); return; }
            const form = new FormData();
            for (const file of files) form.append('files', file);
            const resultDiv = document.getElementById('result');
            resultDiv.style.display = 'block';
            resultDiv.textContent = 'Uploading...';
            try {
                const resp = await fetch('/api/applications/' + encodeURIComponent(appId) + '/documents', { method: 'POST', body: form });
                const data = await resp.json();
                resultDiv.textContent = JSON.stringify(data, null, 2);
            } catch (e) {
                resultDiv.textContent = 'Error: ' + e.message;
            }
        }
    </script>
</body>
</html>
""")


@app.post(
    "/api/applications/{application_id}/documents",
    tags=["Applications"],
    summary="Upload borrower documents",
    description="""
Upload one or more source files for an existing application.

**Supported formats:** PDF, DOCX, DOC, XLSX, XLS, CSV, PNG, JPG, JPEG, TIFF, BMP.

- `application_id` must match an application created via `POST /api/applications`.
- You can upload multiple files in a single request by selecting several files in the form.
- Uploaded files are stored in the application workspace and will be processed when the pipeline runs.

> **Tip:** Use the [/upload](/upload) form in your browser if you prefer a graphical interface.
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                                "description": "Upload borrower documents (PDF, DOCX, XLSX, images)",
                            }
                        },
                        "required": ["files"],
                    }
                }
            },
            "required": True,
        }
    },
)
async def upload_documents(application_id: str, files: List[UploadFile] = File(...)):
    try:
        buffered_files = []
        for upload in files:
            buffered_files.append((upload.filename or "document", await upload.read()))
        return service.save_uploaded_documents(application_id, buffered_files)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/api/orchestrator/run-from-db",
    status_code=202,
    tags=["Pipeline"],
    summary="Run CAM pipeline from DB record",
    description="""
Starts the CAM generation pipeline using a pre-uploaded enriched-extraction
record from the database.

**Returns `202 Accepted` immediately** with a generated `application_id`.
Use that ID to poll `GET /api/orchestrator/{application_id}/status` and then
access the draft and export endpoints exactly as in the normal flow.

### What happens internally
1. A new workspace and application record are created automatically.
2. The enriched JSON and source documents are fetched from PostgreSQL using `record_id`.
3. Source documents are saved to the workspace `input_docs/` directory so citations work.
4. Analysis (Agent 3) runs against the DB JSON.
5. CAM draft is generated and exported to DOCX and PDF.

### Seeding the database
Use `db_uploader.py` to upload the JSON and source files before calling this endpoint:
```
python db_uploader.py --record-id rec_1 --json enriched.json --files file1.pdf file2.xlsx
```

### JSON format
All monetary values must be in **crores (INR)**. The JSON must follow the structure
defined in `enriched_extraction_template.json` (provided alongside this service).

Top-level keys:

| Key | Type | Description |
|---|---|---|
| `overview` | object | Company identity + top-level financial metrics |
| `balance_sheet` | array | One entry per year; use `year` (e.g. `"2026"`) and `period_label` for display |
| `income_statement` | array | One entry per year; include `ebitda` and `net_profit_pat` directly |
| `cash_flow` | array | One entry per year; `operating_activities`, `investing_activities`, `financing_activities` are objects with a `net_cash_from_*` key |
| `bank_statements` | object | Has a `transactions` array; use `credit_cr` / `debit_cr` fields (in crores) |
| `gst_data` | object | Use `gstr1_annual` / `gstr3b_annual` structure; all monetary fields in crores with `_cr` suffix |
| `itr_data` | object | ITR filing details per assessment year |
| `cibil_data` | object | Company CIBIL rank and credit score |
| `promoter_profiles` | array | One entry per promoter/director |

### Request fields
- **record_id** *(required)* — identifies the DB record, e.g. `rec_1`.
- **company_name** — overrides the company name in the JSON (optional).
- **generate_draft** — set `false` to stop after analysis without producing a CAM draft.
- **use_ai_writer** — set `true` to enrich the draft with Gemini prose (requires `GEMINI_API_KEY`).
""",
)
def run_pipeline_from_db(payload: PipelineFromDbRequest, background_tasks: BackgroundTasks):
    from db_client import find_application_by_record_id

    company_name = payload.company_name or f"db-record-{payload.record_id}"

    # Reuse existing application_id if this record_id was already processed
    existing = find_application_by_record_id(payload.record_id)
    if existing:
        application_id = existing["application_id"]
    else:
        application_id = service._generate_application_id(company_name)
        from models import ApplicationCreateRequest
        service.create_application(
            ApplicationCreateRequest(
                application_id=application_id,
                company_name=company_name,
            )
        )

    queued = service.queue_pipeline_from_db(payload, application_id)
    background_tasks.add_task(service.run_pipeline_from_db, payload, application_id)
    return {
        "application_id": application_id,
        "status": queued.status,
        "message": "Pipeline queued. Running CAM generation from DB record.",
        "poll_url": f"/api/orchestrator/{application_id}/status",
    }


@app.post(
    "/api/orchestrator/run",
    status_code=202,
    tags=["Pipeline"],
    summary="Start the analysis pipeline",
    description="""
Queues and starts the four-stage CAM generation pipeline for the given application.

**Returns `202 Accepted` immediately** — the pipeline runs in the background.
Poll `GET /api/orchestrator/{application_id}/status` to track progress.

### Pipeline stages (in order)
| Stage | What it does |
|---|---|
| `transformation` | Parses every uploaded document and extracts raw structured data |
| `enrichment` | Normalises, cross-references, and fills gaps in the extracted data |
| `analysis` | Computes financial ratios, flags risks, and builds insight tables |
| `cam_generation` | Assembles the final memo draft with section blocks and citations |

### Request fields
- **application_id** *(required)* — returned by `POST /api/applications`.
- **company_name** — overrides the name stored in the application record (optional).
- **input_documents_dir** — absolute path to documents folder; defaults to the workspace `input_docs` directory.
- **generate_draft** — set `false` to run only analysis without producing a CAM draft.
- **use_ai_writer** — set `true` to use Gemini for richer section prose (requires `GEMINI_API_KEY` in `.env`).
""",
)
def run_pipeline(payload: PipelineRunRequest, background_tasks: BackgroundTasks):
    try:
        queued = service.queue_pipeline(payload)
        background_tasks.add_task(service.run_pipeline, payload)
        return queued
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get(
    "/api/orchestrator/{application_id}/status",
    tags=["Pipeline"],
    summary="Get pipeline status",
    description="""
Returns the current state of the pipeline for the given application.

### Status values
| Value | Meaning |
|---|---|
| `queued` | Pipeline is scheduled but not yet running |
| `running` | A stage is currently in progress (`current_stage` shows which one) |
| `success` | All stages completed successfully — draft and exports are ready |
| `partial_success` | Pipeline finished but one or more stages had warnings |
| `failed` | Pipeline encountered a fatal error (see `errors` array) |

Poll this endpoint every few seconds after calling `POST /api/orchestrator/run`
and proceed once `status` is `success` or `partial_success`.
""",
)
def get_status(application_id: str):
    try:
        return service.get_status(application_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get(
    "/api/orchestrator/{application_id}/tabs",
    tags=["Results"],
    summary="Get all analytical tabs",
    description="""
Returns the full analytical tabs bundle for a completed pipeline run.

The bundle contains structured output from every analysis stage — financial summaries,
ratio tables, document extracts, and risk flags — organised as named tabs.

Requires the pipeline to have reached `success` or `partial_success` status.
""",
)
def get_tabs(application_id: str):
    try:
        return service.get_tabs_bundle(application_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/orchestrator/{application_id}/tabs/insights",
    tags=["Results"],
    summary="Get key insights and risk flags",
    description="""
Returns the high-level insights and risk flags extracted during the analysis stage.

Also accessible at the alias `/api/orchestrator/{application_id}/insights`.

Typical response includes:
- Key financial highlights
- Identified risk factors and mitigants
- Red-flag alerts (if any)

Requires the pipeline to have completed the `analysis` stage.
""",
)
@app.get("/api/orchestrator/{application_id}/insights", include_in_schema=False)
def get_insights(application_id: str):
    try:
        return service.get_insights(application_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/orchestrator/{application_id}/draft",
    tags=["Results"],
    summary="Get the CAM draft",
    description="""
Returns the structured Credit Assessment Memo draft.

The response is a `CamDraft` object with:
- **sections** — ordered list of memo sections (e.g. *Executive Summary*, *Financial Analysis*)
  - each section contains **blocks** — individual editable paragraphs
  - each block carries **citations** — evidence references pointing back to the source documents
- **source_documents** — list of documents used to generate the memo
- **notes** — any auto-generated notes or warnings

Use `PATCH /api/orchestrator/{application_id}/draft/block` to edit individual blocks
before exporting.
""",
)
def get_draft(application_id: str):
    try:
        return service.load_draft(application_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch(
    "/api/orchestrator/{application_id}/draft/block",
    tags=["Results"],
    summary="Edit a draft block",
    description="""
Overwrites the text of a single block within the CAM draft.

Use the `GET /draft` endpoint first to discover the correct `section_id` and `block_id` values.

### Request body
- **section_id** — ID of the section that contains the block (e.g. `financial_analysis`).
- **block_id** — ID of the specific block to update (e.g. `revenue_summary`).
- **text** — new plain-text content for the block (Markdown supported).

Changes are persisted in the workspace and will be reflected in subsequent export calls.
""",
)
def update_block(application_id: str, payload: DraftUpdateRequest):
    try:
        return service.update_block_text(application_id, payload)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/orchestrator/{application_id}/export/docx",
    tags=["Export"],
    summary="Export CAM as Word document",
    description="""
Downloads the current CAM draft as a formatted **Word (.docx)** file.

The exported document reflects any edits made via `PATCH /draft/block`.
The pipeline must have completed (`status: success` or `partial_success`) before calling this endpoint.

The response is a file download with `Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document`.
""",
)
def export_docx(application_id: str):
    try:
        data = service.export_current_docx_bytes(application_id)
        return StreamingResponse(
            iter([data]),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename=cam_draft_{application_id}.docx"},
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/orchestrator/{application_id}/export/pdf",
    tags=["Export"],
    summary="Export CAM as PDF",
    description="""
Downloads the current CAM draft as a **PDF** file.

The exported document reflects any edits made via `PATCH /draft/block`.
The pipeline must have completed (`status: success` or `partial_success`) before calling this endpoint.

The response is a file download with `Content-Type: application/pdf`.
""",
)
def export_pdf(application_id: str):
    try:
        data = service.export_current_pdf_bytes(application_id)
        return StreamingResponse(
            iter([data]),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=cam_draft_{application_id}.pdf"},
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# Inline preview types — browser renders these directly
_INLINE_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/bmp", "image/tiff"}


@app.get(
    "/api/files/{application_id}/{document_name}",
    tags=["Documents"],
    summary="Fetch a source document from the database",
    description="""
Retrieves a source document stored in `cam_source_files` and streams it to the client.

- **PDF and image files** are returned with `Content-Disposition: inline` so the browser renders them directly.
- **All other files** (DOCX, XLSX, CSV, etc.) are returned with `Content-Disposition: attachment` to trigger a download.

The `application_id` must correspond to a bypass-flow application (created via `POST /api/orchestrator/run-from-db`).
""",
)
def serve_source_file(application_id: str, document_name: str):
    from db_client import fetch_file_from_db, get_record_id_for_application

    record_id = get_record_id_for_application(application_id)
    if record_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"No DB application found for application_id='{application_id}'.",
        )

    try:
        file_bytes, mime_type = fetch_file_from_db(record_id, document_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    disposition = "inline" if mime_type in _INLINE_MIME_TYPES else f"attachment; filename=\"{document_name}\""
    return Response(
        content=file_bytes,
        media_type=mime_type,
        headers={"Content-Disposition": disposition},
    )


@app.post(
    "/api/pdf/segment",
    tags=["Documents"],
    summary="Segment a PDF into single-page PDFs and download as ZIP",
    description="""
Upload any PDF file. The endpoint splits it into individual single-page PDFs and returns
them as a single ZIP archive named `{original_filename}_pages.zip`.

Each entry inside the ZIP follows the pattern `{stem}_page_001.pdf`, `{stem}_page_002.pdf`, etc.
""",
)
async def segment_pdf(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a PDF.")

    pdf_bytes = await file.read()

    try:
        pages = pdf_segmentation_service.split_pdf(pdf_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read PDF: {exc}") from exc

    stem = file.filename[:-4] if file.filename.lower().endswith(".pdf") else file.filename
    zip_bytes = pdf_segmentation_service.build_zip(pages, stem)

    return StreamingResponse(
        iter([zip_bytes]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{stem}_pages.zip"'},
    )
