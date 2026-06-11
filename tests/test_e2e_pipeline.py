"""Real end-to-end integration test: document upload → pipeline → CAM generation.

Requirements to run:
  - GROQ_API_KEY in .env (project root) or already set in the environment
  - Sample borrower documents present at SAMPLE_DOCS_DIR
  - All Python dependencies installed (docling, groq, openpyxl, python-docx, pypdf, reportlab)
  - 15-30 minutes of wall-clock time (real LLM calls over 20 documents)

Skip behaviour:
  - RUN_E2E_TESTS != "1"  → entire module skipped (prevents accidental run in fast suite)
  - GROQ_API_KEY absent   → entire module skipped
  - Sample docs absent    → entire module skipped

Run:
  set RUN_E2E_TESTS=1
  python -m pytest tests/test_e2e_pipeline.py -v -s --timeout=3600

Route reference (what actually exists in main.py):
  POST   /api/applications                                  create application
  POST   /api/applications/{id}/documents                   upload files
  POST   /api/orchestrator/run                              run pipeline (queued)
  GET    /api/orchestrator/{id}/status                      poll status
  GET    /api/orchestrator/{id}/tabs                        financial tab data
  GET    /api/orchestrator/{id}/draft                       load draft JSON
  PATCH  /api/orchestrator/{id}/draft/block                 edit a block
  GET    /api/orchestrator/{id}/export/docx                 download DOCX
  GET    /api/orchestrator/{id}/export/pdf                  download PDF
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

# ── Load .env before anything else so GROQ_API_KEY is available ──────────────
_ENV_FILE = Path(__file__).parent.parent / ".env"
if _ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=_ENV_FILE, override=False)
    except ImportError:
        for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if _k.strip() not in os.environ:
                    os.environ[_k.strip()] = _v.strip()

# ── Skip gates ────────────────────────────────────────────────────────────────
_SAMPLE_DOCS_DIR = Path(
    r"C:\Users\abdula\Downloads\transformation_agent 1\transformation_agent\input_docs"
)

if os.environ.get("RUN_E2E_TESTS", "").strip() != "1":
    pytest.skip(
        "Set RUN_E2E_TESTS=1 to run real end-to-end tests",
        allow_module_level=True,
    )

_GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()
if not _GROQ_KEY:
    pytest.skip(
        "GROQ_API_KEY not set in environment or .env",
        allow_module_level=True,
    )

if not _SAMPLE_DOCS_DIR.exists():
    pytest.skip(
        f"Sample docs not found at {_SAMPLE_DOCS_DIR}",
        allow_module_level=True,
    )

# ── Normal imports ────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from main import app

# ── Constants ─────────────────────────────────────────────────────────────────
PIPELINE_TIMEOUT_S = 3600  # 60 min — 20 documents through LLM extraction is slow
POLL_INTERVAL_S    = 15    # check status every 15 seconds
TERMINAL_STATUSES  = {"draft_ready", "analysis_complete", "failed", "partial_success"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _poll_until_done(client: TestClient, app_id: str, timeout: int = PIPELINE_TIMEOUT_S) -> dict:
    """Poll GET /api/orchestrator/{id}/status until a terminal status is reached.

    Note: with TestClient, background tasks run synchronously, so the pipeline
    may already be finished before this function is first called — first poll
    returns immediately in that case.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/orchestrator/{app_id}/status")
        assert r.status_code == 200, f"Status endpoint error: {r.text}"
        body = r.json()
        current = body.get("status", "")
        if current in TERMINAL_STATUSES:
            return body
        print(f"  [{current}] stage={body.get('current_stage')} — waiting {POLL_INTERVAL_S}s …")
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"Pipeline did not finish within {timeout}s")


def _upload_all_docs(client: TestClient, app_id: str) -> dict:
    files = []
    for path in _SAMPLE_DOCS_DIR.iterdir():
        if path.is_file():
            files.append(("files", (path.name, path.read_bytes(), "application/octet-stream")))
    assert files, "No files found in SAMPLE_DOCS_DIR"
    r = client.post(f"/api/applications/{app_id}/documents", files=files)
    assert r.status_code == 200, f"Upload failed ({r.status_code}): {r.text}"
    return r.json()


# ── Test class ────────────────────────────────────────────────────────────────

class TestRealPipelineE2E:
    """Sequential end-to-end test. Each method builds on state set by previous ones."""

    _app_id: str = ""
    _final_status: dict = {}

    # ── 01: create application ───────────────────────────────────────────────

    def test_01_create_application(self):
        client = TestClient(app)
        r = client.post("/api/applications", json={
            "company_name": "E2E Test Borrower",
            "loan_amount": 50000000,
            "industry": "Manufacturing",
            "loan_type": "Term Loan",
        })
        assert r.status_code == 200, f"Create failed ({r.status_code}): {r.text}"
        body = r.json()
        assert body.get("application_id"), f"No application_id in response: {body}"
        TestRealPipelineE2E._app_id = body["application_id"]
        print(f"\n  Created application_id={body['application_id']}")

    # ── 02: upload real borrower documents ───────────────────────────────────

    def test_02_upload_documents(self):
        assert TestRealPipelineE2E._app_id, "application_id not set (test_01 failed?)"
        client = TestClient(app)
        result = _upload_all_docs(client, TestRealPipelineE2E._app_id)
        assert result.get("document_count", 0) > 0, f"No documents counted: {result}"
        print(f"  Uploaded {result['document_count']} documents")

    # ── 03: run the full pipeline (real agents + LLM calls) ──────────────────

    def test_03_run_pipeline(self):
        assert TestRealPipelineE2E._app_id, "application_id not set"
        client = TestClient(app)

        # POST /api/orchestrator/run  — queues pipeline as background task.
        # With TestClient, background tasks execute synchronously before the
        # client call returns, so this blocks until the pipeline is finished.
        r = client.post("/api/orchestrator/run", json={
            "application_id": TestRealPipelineE2E._app_id,
            "generate_draft": True,
        })
        assert r.status_code == 202, f"Pipeline run rejected ({r.status_code}): {r.text}"
        print(f"  Pipeline queued — polling for completion (timeout={PIPELINE_TIMEOUT_S}s) …")

        final = _poll_until_done(client, TestRealPipelineE2E._app_id)
        TestRealPipelineE2E._final_status = final
        print(f"  Pipeline finished: status={final['status']}")

        assert final["status"] != "failed", (
            f"Pipeline failed.\n"
            f"Errors: {final.get('errors')}\n"
            f"Stages: {[(s['stage'], s['status']) for s in final.get('stages', [])]}"
        )

    # ── 04: all 4 stage results recorded ─────────────────────────────────────

    def test_04_all_stages_present(self):
        stages = {s["stage"]: s["status"] for s in TestRealPipelineE2E._final_status.get("stages", [])}
        for expected in ("transformation", "enrichment", "analysis", "cam_generation"):
            assert expected in stages, f"Stage '{expected}' missing. Got: {stages}"
        print(f"  Stages: {stages}")

    # ── 05: transformation output artifact on disk ────────────────────────────

    def test_05_transformation_output_artifact(self):
        artifacts = TestRealPipelineE2E._final_status.get("artifacts", {})
        t_path = artifacts.get("transformation_output")
        assert t_path, f"transformation_output artifact missing from status. Artifacts: {artifacts}"
        path = Path(t_path)
        assert path.exists(), f"transformation_output.json not on disk: {path}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "tab_data" in data or "overview" in data, (
            f"transformation_output.json has unexpected structure: {list(data.keys())}"
        )

    # ── 06: enrichment output artifact ───────────────────────────────────────

    def test_06_enrichment_output_artifact(self):
        artifacts = TestRealPipelineE2E._final_status.get("artifacts", {})
        e_path = artifacts.get("enrichment_output")
        assert e_path, f"enrichment_output artifact missing. Artifacts: {artifacts}"
        assert Path(e_path).exists(), f"enrich_output.json not on disk: {e_path}"

    # ── 07: CAM draft artifact on disk with sections ──────────────────────────

    def test_07_cam_draft_artifact(self):
        artifacts = TestRealPipelineE2E._final_status.get("artifacts", {})
        draft_path = artifacts.get("cam_draft_output")
        assert draft_path, f"cam_draft_output artifact missing. Artifacts: {artifacts}"
        path = Path(draft_path)
        assert path.exists(), f"cam_draft.json not on disk: {path}"
        draft = json.loads(path.read_text(encoding="utf-8"))
        assert draft.get("company_name"), f"Draft missing company_name: {list(draft.keys())}"
        assert len(draft.get("sections", [])) > 0, "Draft has no sections"
        print(f"  Draft: company={draft['company_name']}, sections={len(draft['sections'])}")

    # ── 08: status endpoint shows draft_ready ────────────────────────────────

    def test_08_status_is_draft_ready(self):
        client = TestClient(app)
        r = client.get(f"/api/orchestrator/{TestRealPipelineE2E._app_id}/status")
        assert r.status_code == 200, f"Status endpoint failed: {r.text}"
        body = r.json()
        assert body["status"] == "draft_ready", (
            f"Expected draft_ready, got {body['status']!r}"
        )
        assert body.get("draft_available") is True, "draft_available should be True"

    # ── 09: tabs endpoint returns financial data ──────────────────────────────

    def test_09_tabs_endpoint_has_data(self):
        client = TestClient(app)
        r = client.get(f"/api/orchestrator/{TestRealPipelineE2E._app_id}/tabs")
        assert r.status_code == 200, f"Tabs endpoint failed ({r.status_code}): {r.text}"
        body = r.json()
        assert "overview" in body, f"'overview' missing from tabs response: {list(body.keys())}"
        print(f"  Tabs keys: {list(body.keys())}")

    # ── 10: income_statement has extracted rows ───────────────────────────────

    def test_10_income_statement_not_empty(self):
        client = TestClient(app)
        r = client.get(f"/api/orchestrator/{TestRealPipelineE2E._app_id}/tabs")
        body = r.json()
        income = body.get("income_statement", [])
        assert len(income) > 0, (
            "income_statement is empty — Agent 1 may have failed to extract financials, "
            "or _effective_tabs silently discarded Agent 1 data"
        )
        print(f"  income_statement: {len(income)} row(s), first={income[0]}")

    # ── 11: draft endpoint returns valid JSON ─────────────────────────────────

    def test_11_draft_endpoint(self):
        client = TestClient(app)
        r = client.get(f"/api/orchestrator/{TestRealPipelineE2E._app_id}/draft")
        assert r.status_code == 200, f"Draft endpoint failed ({r.status_code}): {r.text}"
        draft = r.json()
        assert draft.get("application_id") == TestRealPipelineE2E._app_id
        assert len(draft.get("sections", [])) > 0, "Draft returned from API has no sections"
        print(f"  Draft via API: {len(draft['sections'])} sections")

    # ── 12: edit a draft block ────────────────────────────────────────────────

    def test_12_edit_draft_block(self):
        client = TestClient(app)
        # Load draft to find first editable block
        r = client.get(f"/api/orchestrator/{TestRealPipelineE2E._app_id}/draft")
        assert r.status_code == 200
        draft = r.json()

        if not draft.get("sections") or not draft["sections"][0].get("blocks"):
            pytest.skip("Draft has no blocks to edit")

        section = draft["sections"][0]
        block = section["blocks"][0]
        new_text = "Manually edited by E2E integration test."

        r2 = client.patch(
            f"/api/orchestrator/{TestRealPipelineE2E._app_id}/draft/block",
            json={
                "section_id": section["id"],
                "block_id": block["id"],
                "text": new_text,
            },
        )
        assert r2.status_code == 200, f"Draft block edit failed ({r2.status_code}): {r2.text}"

        # Verify the edit persisted by re-fetching
        r3 = client.get(f"/api/orchestrator/{TestRealPipelineE2E._app_id}/draft")
        updated = r3.json()
        updated_text = updated["sections"][0]["blocks"][0]["text"]
        assert updated_text == new_text, f"Edit did not persist. Got: {updated_text!r}"
        print(f"  Block edit persisted for block {block['id']}")

    # ── 13: export to DOCX ────────────────────────────────────────────────────

    def test_13_export_docx(self):
        client = TestClient(app)
        r = client.get(f"/api/orchestrator/{TestRealPipelineE2E._app_id}/export/docx")
        assert r.status_code == 200, f"DOCX export failed ({r.status_code}): {r.text}"
        content = r.content
        assert content[:2] == b"PK", f"Response is not a valid OOXML/ZIP (got {content[:4]!r})"
        print(f"  DOCX export: {len(content) // 1024} KB")

    # ── 14: export to PDF ─────────────────────────────────────────────────────

    def test_14_export_pdf(self):
        client = TestClient(app)
        r = client.get(f"/api/orchestrator/{TestRealPipelineE2E._app_id}/export/pdf")
        assert r.status_code == 200, f"PDF export failed ({r.status_code}): {r.text}"
        content = r.content
        assert content[:5] == b"%PDF-", f"Response is not a valid PDF (got {content[:8]!r})"
        print(f"  PDF export: {len(content) // 1024} KB")
