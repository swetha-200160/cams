# CAMS API Reference

**CAMS — Credit Assessment & Memo System**
Automates end-to-end generation of Credit Assessment Memos (CAMs) from raw borrower documents.

Interactive docs: `/docs` (Swagger UI) | `/redoc` (ReDoc)

---

## Workflows

### Normal flow — upload documents and run
```
1. POST /api/applications                       → create application, get application_id
2. POST /api/applications/{id}/documents        → upload borrower files
3. POST /api/orchestrator/run                   → start pipeline (returns 202)
4. GET  /api/orchestrator/{id}/status           → poll until success/partial_success
5. GET  /api/orchestrator/{id}/draft            → review the CAM draft
6. PATCH /api/orchestrator/{id}/draft/block     → (optional) edit a block
7. GET  /api/orchestrator/{id}/export/docx      → download Word file
   GET  /api/orchestrator/{id}/export/pdf       → download PDF
```

### DB flow — run from pre-loaded record
```
1. python db_uploader.py --record-id rec_1 --json enriched.json --files *.pdf *.xlsx
2. POST /api/orchestrator/run-from-db           → start pipeline (returns 202)
3. GET  /api/orchestrator/{id}/status           → poll until success/partial_success
4. GET  /api/orchestrator/{id}/draft            → review the CAM draft
5. PATCH /api/orchestrator/{id}/draft/block     → (optional) edit a block
6. GET  /api/orchestrator/{id}/export/docx      → download Word file
   GET  /api/orchestrator/{id}/export/pdf       → download PDF
```

> `application_id` follows the pattern `<company-slug>-<8-hex-chars>`, e.g. `abc-enterprises-pvt-ltd-656b8b83`.
> Re-running `run-from-db` with the same `record_id` reuses the existing `application_id`.

---

## System

### `GET /health`
Health check. Returns `ok` if the service is reachable.

```json
{ "status": "ok", "service": "CAMS", "version": "1.0.0" }
```

---

### `GET /`
Service metadata and links to API documentation.

```json
{ "service": "CAMS", "version": "1.0.0", "mode": "backend-only", "docs": "/docs" }
```

---

## Applications

### `POST /api/applications`
Register a new loan application. Returns the `application_id` used in all subsequent calls.

**Request body (JSON)**

| Field | Required | Description |
|---|---|---|
| `company_name` | Yes | Legal name of the borrower entity |
| `application_id` | No | Leave blank to auto-generate (recommended) |
| `loan_amount` | No | Requested loan amount in base currency |
| `loan_type` | No | e.g. `term_loan`, `working_capital`, `overdraft` |
| `industry` | No | Sector classification |
| `application_date` | No | ISO date `YYYY-MM-DD`; defaults to today |

**Example**
```json
{ "company_name": "ABC Enterprises Pvt Ltd", "loan_amount": 5000000, "loan_type": "term_loan" }
```

**Response**
```json
{ "application_id": "abc-enterprises-pvt-ltd-656b8b83", "company_name": "ABC Enterprises Pvt Ltd", "status": "created" }
```

**Errors:** `409` — application ID already exists

---

### `POST /api/applications/{application_id}/documents`
Upload one or more borrower source files.

**Supported formats:** PDF, DOCX, DOC, XLSX, XLS, CSV, PNG, JPG, JPEG, TIFF, BMP

```bash
curl -X POST "http://localhost:8000/api/applications/{id}/documents" \
  -F "files=@financials.pdf" -F "files=@bank_statement.xlsx"
```

**Errors:** `404` — application not found

> Use the browser form at `/upload` for a graphical upload interface.

---

### `GET /upload`
Browser-based HTML form for uploading documents without a REST client.

---

## Pipeline

### `POST /api/orchestrator/run-from-db`
Run CAM generation from a pre-loaded DB record. **Returns `202 Accepted` immediately.**

Fetches the enriched JSON and source documents from PostgreSQL, runs analysis, and generates the CAM draft. No need to create an application or upload files separately.

**Request body (JSON)**

| Field | Required | Description |
|---|---|---|
| `record_id` | Yes | ID of the DB record, e.g. `rec_1` |
| `company_name` | No | Overrides the company name from the JSON |
| `generate_draft` | No | `false` to skip draft generation (default: `true`) |
| `use_ai_writer` | No | `true` for Gemini-powered prose (requires `GEMINI_API_KEY`) |

**Example**
```json
{ "record_id": "rec_1" }
```

**Response**
```json
{
  "application_id": "abc-enterprises-pvt-ltd-656b8b83",
  "status": "queued",
  "message": "Pipeline queued. Running CAM generation from DB record.",
  "poll_url": "/api/orchestrator/abc-enterprises-pvt-ltd-656b8b83/status"
}
```

**Pipeline stages**

| Stage | Description |
|---|---|
| `transformation` | Data loaded from DB record |
| `enrichment` | Data enriched from DB record |
| `analysis` | Financial ratios, risk flags, and insights computed |
| `cam_generation` | CAM draft assembled with 18 sections and citations |

**Seeding the database**
```bash
python db_uploader.py \
  --record-id rec_1 \
  --json path/to/enriched_extraction.json \
  --files path/to/file1.pdf path/to/file2.xlsx ...
```

**JSON format** — see `enriched_extraction_template.json` for the full structure.
All monetary values must be in **crores (INR)**. Key top-level fields:

| Field | Type | Description |
|---|---|---|
| `overview` | object | Company identity and top-level metrics (`net_sales`, `ebitda`, `pat`, `networth`, `total_debt`) |
| `balance_sheet` | array | One entry per year. Use `year` key (e.g. `"2026"`) and `period_label` for display |
| `income_statement` | array | One entry per year. Include `ebitda` and `net_profit_pat` directly on the row |
| `cash_flow` | array | One entry per year. `operating_activities`, `investing_activities`, `financing_activities` are objects with a `net_cash_from_*` key |
| `bank_statements` | object | Contains a `transactions` array. Use `credit_cr` / `debit_cr` fields (in crores) |
| `gst_data` | object | Use `gstr1_annual` / `gstr3b_annual` structure; all monetary fields with `_cr` suffix |
| `itr_data` | object | ITR filing details per assessment year |
| `cibil_data` | object | Company CIBIL rank and credit score |
| `promoter_profiles` | array | One entry per promoter/director |

**Errors:** `404` — record not found | `500` — pipeline error

---

### `POST /api/orchestrator/run`
Start the full four-stage pipeline from uploaded documents. **Returns `202 Accepted` immediately.**

**Request body (JSON)**

| Field | Required | Description |
|---|---|---|
| `application_id` | Yes | From `POST /api/applications` |
| `company_name` | No | Overrides the stored company name |
| `input_documents_dir` | No | Absolute path to documents folder; defaults to workspace `input_docs` dir |
| `generate_draft` | No | `false` to skip draft generation |
| `use_ai_writer` | No | `true` for Gemini-powered prose (requires `GEMINI_API_KEY`) |

**Pipeline stages**

| Stage | Description |
|---|---|
| `transformation` | Parses every uploaded document and extracts raw structured data |
| `enrichment` | Normalises, cross-references, and fills gaps in extracted data |
| `analysis` | Computes financial ratios, flags risks, builds insight tables |
| `cam_generation` | Assembles the final memo draft with sections, blocks, and citations |

**Errors:** `404` — application not found | `500` — pipeline error

---

### `GET /api/orchestrator/{application_id}/status`
Poll pipeline status. Check every few seconds after calling `/run` or `/run-from-db`.

**Status values**

| Value | Meaning |
|---|---|
| `queued` | Scheduled but not yet running |
| `running` | A stage is in progress (`current_stage` shows which one) |
| `success` | All stages completed — draft and exports ready |
| `partial_success` | Finished with warnings in one or more stages |
| `failed` | Fatal error — see `errors` array |

**Example response**
```json
{
  "application_id": "abc-enterprises-pvt-ltd-656b8b83",
  "company_name": "ABC Enterprises Pvt Ltd",
  "status": "success",
  "current_stage": null,
  "stages": [
    { "stage": "transformation", "status": "success" },
    { "stage": "enrichment",     "status": "success" },
    { "stage": "analysis",       "status": "success" },
    { "stage": "cam_generation", "status": "success" }
  ],
  "errors": [],
  "draft_available": true
}
```

---

## Results

### `GET /api/orchestrator/{application_id}/draft`
Returns the structured CAM draft with 18 sections.

**Response structure**

| Field | Description |
|---|---|
| `sections` | Ordered list of memo sections |
| `sections[].id` | Section ID — use this in `PATCH /draft/block` |
| `sections[].blocks` | Editable paragraphs within the section |
| `sections[].blocks[].id` | Block ID — use this in `PATCH /draft/block` |
| `sections[].blocks[].text` | Current text content of the block |
| `sections[].blocks[].citations` | Evidence references back to source documents |

**Draft sections (in order)**

| section_id | Title |
|---|---|
| `executive_summary` | Executive Summary |
| `borrower_profile` | Borrower Profile |
| `promoter_profile` | Promoter Profile |
| `group_company_analysis` | Group Company Analysis |
| `industry_analysis` | Industry Analysis |
| `business_model_assessment` | Business Model Assessment |
| `financial_statement_analysis` | Financial Statement Analysis |
| `banking_analysis` | Banking Analysis |
| `gst_analysis` | GST Analysis |
| `credit_bureau_analysis` | Credit Bureau Analysis |
| `collateral_analysis` | Collateral Analysis |
| `legal_compliance_review` | Legal & Compliance Review |
| `risk_assessment` | Risk Assessment |
| `loan_structuring` | Loan Structuring |
| `early_warning_signals` | Early Warning Signals |
| `credit_recommendation` | Credit Recommendation |
| `credit_committee_notes` | Credit Committee Notes |
| `annexures` | Annexures |

---

### `PATCH /api/orchestrator/{application_id}/draft/block`
Overwrite the text of a single block. Changes persist and are reflected in exports.

**Request body (JSON)**

| Field | Required | Description |
|---|---|---|
| `section_id` | Yes | Section ID from `GET /draft` (e.g. `executive_summary`) |
| `block_id` | Yes | Block ID from `GET /draft` (e.g. `blk_1`) |
| `text` | Yes | New content (Markdown supported) |

**Example**
```json
{
  "section_id": "executive_summary",
  "block_id": "blk_1",
  "text": "ABC Enterprises is a strong credit with DSCR of 5.2x and zero leverage."
}
```

**Errors:** `404` — section or block ID not found

---

### `GET /api/orchestrator/{application_id}/tabs`
Full analytical tabs bundle — financial summaries, ratio tables, balance sheet, income statement, cash flow, and risk flags.

**Errors:** `404` — pipeline not complete

---

### `GET /api/orchestrator/{application_id}/tabs/insights`
### `GET /api/orchestrator/{application_id}/insights` *(alias)*
Key insights and risk flags from the analysis stage. Includes ratio report, GST analytics, banking behaviour, cash flow projection, and market risk.

**Errors:** `404` — analysis not complete

---

## Export

### `GET /api/orchestrator/{application_id}/export/docx`
Download the CAM draft as a **Word (.docx)** file. Reflects any edits made via `PATCH /draft/block`.

- `Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- Pipeline must be `success` or `partial_success`

**Errors:** `404` — export not available

---

### `GET /api/orchestrator/{application_id}/export/pdf`
Download the CAM draft as a **PDF** file. Reflects any edits made via `PATCH /draft/block`.

- `Content-Type: application/pdf`
- Pipeline must be `success` or `partial_success`

**Errors:** `404` — export not available

---

## Quick Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/` | Service info |
| GET | `/upload` | Browser upload form |
| POST | `/api/applications` | Create loan application |
| POST | `/api/applications/{id}/documents` | Upload borrower documents |
| POST | `/api/orchestrator/run-from-db` | Run CAM from DB record |
| POST | `/api/orchestrator/run` | Run full pipeline from uploaded documents |
| GET | `/api/orchestrator/{id}/status` | Get pipeline status |
| GET | `/api/orchestrator/{id}/draft` | Get CAM draft |
| PATCH | `/api/orchestrator/{id}/draft/block` | Edit a draft block |
| GET | `/api/orchestrator/{id}/tabs` | Get analytical tabs bundle |
| GET | `/api/orchestrator/{id}/tabs/insights` | Get key insights and risk flags |
| GET | `/api/orchestrator/{id}/export/docx` | Download as Word (.docx) |
| GET | `/api/orchestrator/{id}/export/pdf` | Download as PDF |
