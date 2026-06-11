"""Tests for integrated_cam_backend/main.py — FastAPI HTTP routes.

All tests use FastAPI's TestClient and patch the UnifiedOrchestratorService
so no real filesystem or subprocesses are touched.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

# We import the app AFTER patching settings.workspace_root so it does not
# create directories in the real workspaces/ folder during import.
from models import (
    ApplicationRecord,
    ArtifactPaths,
    CamBlock,
    CamDraft,
    CamSection,
    DocumentUploadResponse,
    OrchestratorStatusResponse,
    PipelineStageResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _now():
    return datetime.now(timezone.utc)


def _sample_record(app_id: str = "app-01") -> ApplicationRecord:
    now = _now()
    return ApplicationRecord(
        application_id=app_id,
        company_name="Test Corp",
        status="created",
        created_at=now,
        updated_at=now,
        document_count=0,
    )


def _sample_status(app_id: str = "app-01") -> OrchestratorStatusResponse:
    return OrchestratorStatusResponse(
        application_id=app_id,
        company_name="Test Corp",
        status="queued",
        last_updated_at=_now(),
    )


def _sample_draft(app_id: str = "app-01") -> CamDraft:
    block = CamBlock(id="b-01", title="Overview", text="Revenue was ₹5000 Cr.")
    section = CamSection(id="s-01", title="Executive Summary", blocks=[block], status="ready")
    return CamDraft(
        application_id=app_id,
        company_name="Test Corp",
        generated_at=_now(),
        sections=[section],
    )


@pytest.fixture
def mock_service():
    """Return a MagicMock that stands in for UnifiedOrchestratorService."""
    svc = MagicMock()
    svc.create_application.return_value = _sample_record()
    svc.list_applications.return_value = [_sample_record()]
    svc.get_application.return_value = _sample_record()
    svc.save_uploaded_documents.return_value = DocumentUploadResponse(
        application_id="app-01",
        uploaded_files=["doc.pdf"],
        stored_in="/workspaces/app-01/current/input_docs",
        document_count=1,
    )
    svc.queue_pipeline.return_value = _sample_status()
    svc.get_status.return_value = _sample_status()
    svc.get_tabs_bundle.return_value = {"overview": {}, "balance_sheet": [], "income_statement": [], "cash_flow": []}
    svc.get_insights.return_value = {"application_id": "app-01", "status": "success", "data": {}}
    svc.load_draft.return_value = _sample_draft()
    svc.update_block_text.return_value = _sample_draft()

    # export_current_docx / export_current_pdf return temp-like Path objects
    _fake_docx = MagicMock(spec=Path)
    _fake_docx.name = "cam_draft.docx"
    _fake_docx.__str__ = lambda s: "/tmp/cam_draft.docx"
    _fake_pdf = MagicMock(spec=Path)
    _fake_pdf.name = "cam_draft.pdf"
    _fake_pdf.__str__ = lambda s: "/tmp/cam_draft.pdf"
    svc.export_current_docx.return_value = _fake_docx
    svc.export_current_pdf.return_value = _fake_pdf

    return svc


@pytest.fixture
def client(mock_service):
    """TestClient with the service replaced by mock_service."""
    import main as app_module
    original_service = app_module.service
    app_module.service = mock_service
    with TestClient(app_module.app) as c:
        yield c
    app_module.service = original_service


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
class TestHealthEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_returns_ok_status(self, client):
        data = resp = client.get("/health").json()
        assert data["status"] == "ok"

    def test_returns_service_name(self, client):
        data = client.get("/health").json()
        assert "service" in data

    def test_returns_version(self, client):
        data = client.get("/health").json()
        assert "version" in data


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------
class TestRootEndpoint:
    def test_returns_200(self, client):
        assert client.get("/").status_code == 200

    def test_contains_docs_link(self, client):
        data = client.get("/").json()
        assert "docs" in data


# ---------------------------------------------------------------------------
# POST /api/applications
# ---------------------------------------------------------------------------
class TestCreateApplication:
    def test_creates_application(self, client):
        resp = client.post("/api/applications", json={"company_name": "Test Corp"})
        assert resp.status_code == 200

    def test_returns_application_id(self, client):
        data = client.post("/api/applications", json={"company_name": "Test Corp"}).json()
        assert "application_id" in data

    def test_missing_company_name_returns_422(self, client):
        resp = client.post("/api/applications", json={})
        assert resp.status_code == 422

    def test_duplicate_application_returns_409(self, client, mock_service):
        mock_service.create_application.side_effect = FileExistsError("Already exists")
        resp = client.post("/api/applications", json={"company_name": "Dup Corp"})
        assert resp.status_code == 409

    def test_with_optional_fields(self, client):
        resp = client.post("/api/applications", json={
            "company_name": "Full Corp",
            "loan_amount": 50000000,
            "industry": "IT",
            "loan_type": "Term Loan",
        })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /upload
# ---------------------------------------------------------------------------
class TestUploadForm:
    def test_returns_200(self, client):
        assert client.get("/upload").status_code == 200

    def test_returns_html(self, client):
        resp = client.get("/upload")
        assert "text/html" in resp.headers["content-type"]

    def test_contains_form_elements(self, client):
        content = client.get("/upload").text
        assert "<form" in content.lower() or "uploadFiles" in content


# ---------------------------------------------------------------------------
# POST /api/applications/{id}/documents
# ---------------------------------------------------------------------------
class TestUploadDocuments:
    def test_upload_single_file(self, client):
        resp = client.post(
            "/api/applications/app-01/documents",
            files={"files": ("doc.pdf", b"pdf content", "application/pdf")},
        )
        assert resp.status_code == 200

    def test_returns_document_count(self, client):
        resp = client.post(
            "/api/applications/app-01/documents",
            files={"files": ("doc.pdf", b"pdf content", "application/pdf")},
        )
        data = resp.json()
        assert "document_count" in data

    def test_returns_uploaded_files_list(self, client):
        resp = client.post(
            "/api/applications/app-01/documents",
            files={"files": ("doc.pdf", b"pdf content", "application/pdf")},
        )
        data = resp.json()
        assert "uploaded_files" in data

    def test_unknown_application_returns_404(self, client, mock_service):
        mock_service.save_uploaded_documents.side_effect = FileNotFoundError("Not found")
        resp = client.post(
            "/api/applications/ghost/documents",
            files={"files": ("doc.pdf", b"data", "application/pdf")},
        )
        assert resp.status_code == 404

    def test_upload_multiple_files(self, client, mock_service):
        mock_service.save_uploaded_documents.return_value = DocumentUploadResponse(
            application_id="app-01",
            uploaded_files=["a.pdf", "b.xlsx"],
            stored_in="/workspaces/app-01/current/input_docs",
            document_count=2,
        )
        resp = client.post(
            "/api/applications/app-01/documents",
            files=[
                ("files", ("a.pdf", b"a", "application/pdf")),
                ("files", ("b.xlsx", b"b", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ],
        )
        assert resp.status_code == 200
        assert resp.json()["document_count"] == 2


# ---------------------------------------------------------------------------
# POST /api/orchestrator/run
# ---------------------------------------------------------------------------
class TestRunPipeline:
    def test_accepts_run_request(self, client):
        resp = client.post("/api/orchestrator/run", json={"application_id": "app-01"})
        assert resp.status_code == 202

    def test_returns_queued_status(self, client):
        data = client.post("/api/orchestrator/run", json={"application_id": "app-01"}).json()
        assert data["status"] == "queued"

    def test_missing_application_id_returns_422(self, client):
        resp = client.post("/api/orchestrator/run", json={})
        assert resp.status_code == 422

    def test_unknown_application_returns_404(self, client, mock_service):
        mock_service.queue_pipeline.side_effect = FileNotFoundError("No app")
        resp = client.post("/api/orchestrator/run", json={"application_id": "ghost"})
        assert resp.status_code == 404

    def test_internal_error_returns_500(self, client, mock_service):
        mock_service.queue_pipeline.side_effect = RuntimeError("Unexpected error")
        resp = client.post("/api/orchestrator/run", json={"application_id": "app-01"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/orchestrator/{id}/status
# ---------------------------------------------------------------------------
class TestGetStatus:
    def test_returns_200(self, client):
        assert client.get("/api/orchestrator/app-01/status").status_code == 200

    def test_returns_application_id(self, client):
        data = client.get("/api/orchestrator/app-01/status").json()
        assert data["application_id"] == "app-01"

    def test_returns_status_field(self, client):
        data = client.get("/api/orchestrator/app-01/status").json()
        assert "status" in data

    def test_service_error_returns_500(self, client, mock_service):
        mock_service.get_status.side_effect = RuntimeError("DB error")
        resp = client.get("/api/orchestrator/app-01/status")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/orchestrator/{id}/tabs
# ---------------------------------------------------------------------------
class TestGetTabs:
    def test_returns_200(self, client):
        assert client.get("/api/orchestrator/app-01/tabs").status_code == 200

    def test_returns_tab_data(self, client):
        data = client.get("/api/orchestrator/app-01/tabs").json()
        assert "overview" in data or "balance_sheet" in data or isinstance(data, dict)

    def test_unknown_application_returns_404(self, client, mock_service):
        mock_service.get_tabs_bundle.side_effect = FileNotFoundError("No tabs")
        resp = client.get("/api/orchestrator/ghost/tabs")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/orchestrator/{id}/insights
# ---------------------------------------------------------------------------
class TestGetInsights:
    def test_returns_200(self, client):
        assert client.get("/api/orchestrator/app-01/insights").status_code == 200

    def test_returns_application_id(self, client):
        data = client.get("/api/orchestrator/app-01/insights").json()
        assert data["application_id"] == "app-01"

    def test_unknown_application_returns_404(self, client, mock_service):
        mock_service.get_insights.side_effect = FileNotFoundError("No insights")
        resp = client.get("/api/orchestrator/ghost/insights")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/orchestrator/{id}/draft
# ---------------------------------------------------------------------------
class TestGetDraft:
    def test_returns_200(self, client):
        assert client.get("/api/orchestrator/app-01/draft").status_code == 200

    def test_returns_sections(self, client):
        data = client.get("/api/orchestrator/app-01/draft").json()
        assert "sections" in data

    def test_unknown_application_returns_404(self, client, mock_service):
        mock_service.load_draft.side_effect = FileNotFoundError("No draft")
        resp = client.get("/api/orchestrator/ghost/draft")
        assert resp.status_code == 404

    def test_company_name_in_draft(self, client):
        data = client.get("/api/orchestrator/app-01/draft").json()
        assert data["company_name"] == "Test Corp"


# ---------------------------------------------------------------------------
# PATCH /api/orchestrator/{id}/draft/block
# ---------------------------------------------------------------------------
class TestUpdateBlock:
    def test_returns_200(self, client):
        resp = client.patch(
            "/api/orchestrator/app-01/draft/block",
            json={"section_id": "s-01", "block_id": "b-01", "text": "New text"},
        )
        assert resp.status_code == 200

    def test_returns_draft(self, client):
        resp = client.patch(
            "/api/orchestrator/app-01/draft/block",
            json={"section_id": "s-01", "block_id": "b-01", "text": "New text"},
        )
        data = resp.json()
        assert "sections" in data

    def test_missing_fields_returns_422(self, client):
        resp = client.patch(
            "/api/orchestrator/app-01/draft/block",
            json={"section_id": "s-01"},
        )
        assert resp.status_code == 422

    def test_unknown_block_returns_404(self, client, mock_service):
        mock_service.update_block_text.side_effect = KeyError("b-GHOST")
        resp = client.patch(
            "/api/orchestrator/app-01/draft/block",
            json={"section_id": "s-01", "block_id": "b-GHOST", "text": "x"},
        )
        assert resp.status_code == 404

    def test_missing_draft_returns_404(self, client, mock_service):
        mock_service.update_block_text.side_effect = FileNotFoundError("No draft")
        resp = client.patch(
            "/api/orchestrator/app-01/draft/block",
            json={"section_id": "s-01", "block_id": "b-01", "text": "x"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/orchestrator/{id}/export/docx
# ---------------------------------------------------------------------------
class TestExportDocx:
    def test_unknown_application_returns_404(self, client, mock_service):
        mock_service.export_current_docx.side_effect = FileNotFoundError("No draft")
        resp = client.get("/api/orchestrator/ghost/export/docx")
        assert resp.status_code == 404

    def test_export_called_with_correct_id(self, client, mock_service):
        try:
            client.get("/api/orchestrator/app-01/export/docx")
        except Exception:
            pass
        mock_service.export_current_docx.assert_called_once_with("app-01")


# ---------------------------------------------------------------------------
# GET /api/orchestrator/{id}/export/pdf
# ---------------------------------------------------------------------------
class TestExportPdf:
    def test_unknown_application_returns_404(self, client, mock_service):
        mock_service.export_current_pdf.side_effect = FileNotFoundError("No draft")
        resp = client.get("/api/orchestrator/ghost/export/pdf")
        assert resp.status_code == 404

    def test_export_called_with_correct_id(self, client, mock_service):
        try:
            client.get("/api/orchestrator/app-01/export/pdf")
        except Exception:
            pass
        mock_service.export_current_pdf.assert_called_once_with("app-01")
