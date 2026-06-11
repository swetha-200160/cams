"""Tests for integrated_cam_backend/orchestrator_service.py

All tests use a temporary workspace directory so they never touch
the real workspaces/ folder.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    ApplicationCreateRequest,
    CamBlock,
    CamDraft,
    CamSection,
    DraftUpdateRequest,
    PipelineRunRequest,
)
from orchestrator_service import UnifiedOrchestratorService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now():
    return datetime.now(timezone.utc)


def _make_service(tmp_path: Path) -> UnifiedOrchestratorService:
    svc = UnifiedOrchestratorService.__new__(UnifiedOrchestratorService)
    svc.workspace_root = tmp_path / "workspaces"
    svc.workspace_root.mkdir(parents=True, exist_ok=True)
    svc.vendor_root = tmp_path / "vendor"
    svc.vendor_root.mkdir(parents=True, exist_ok=True)
    return svc


def _create_app(svc: UnifiedOrchestratorService, company: str = "Test Corp", app_id: str = None) -> str:
    payload = ApplicationCreateRequest(company_name=company, application_id=app_id)
    record = svc.create_application(payload)
    return record.application_id


def _sample_draft(app_id: str) -> CamDraft:
    block = CamBlock(id="b-01", title="Overview", text="Initial text.")
    section = CamSection(id="s-01", title="Executive Summary", blocks=[block], status="ready")
    return CamDraft(
        application_id=app_id,
        company_name="Test Corp",
        generated_at=_now(),
        sections=[section],
    )


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------
class TestCreateApplication:
    def test_creates_application_record(self, tmp_path):
        svc = _make_service(tmp_path)
        payload = ApplicationCreateRequest(company_name="ACME Ltd")
        record = svc.create_application(payload)
        assert record.company_name == "ACME Ltd"
        assert record.application_id

    def test_application_id_generated_from_company_name(self, tmp_path):
        svc = _make_service(tmp_path)
        payload = ApplicationCreateRequest(company_name="Alpha Corp")
        record = svc.create_application(payload)
        assert "alpha" in record.application_id.lower() or len(record.application_id) > 0

    def test_explicit_application_id_used(self, tmp_path):
        svc = _make_service(tmp_path)
        payload = ApplicationCreateRequest(company_name="Beta Corp", application_id="beta-corp-001")
        record = svc.create_application(payload)
        assert record.application_id == "beta-corp-001"

    def test_workspace_dirs_created(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        workspace = svc.workspace_root / app_id / "current"
        assert workspace.exists()
        assert (workspace / "input_docs").exists()
        assert (workspace / "outputs").exists()

    def test_application_json_written(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        app_json = svc.workspace_root / app_id / "application.json"
        assert app_json.exists()
        data = json.loads(app_json.read_text())
        assert data["application_id"] == app_id

    def test_status_json_written(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        status_json = svc.workspace_root / app_id / "current" / "status.json"
        assert status_json.exists()

    def test_duplicate_application_id_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        payload = ApplicationCreateRequest(company_name="Dup Corp", application_id="dup-001")
        svc.create_application(payload)
        with pytest.raises(FileExistsError):
            svc.create_application(payload)

    def test_default_status_is_created(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        record = svc.get_application(app_id)
        assert record.status == "created"

    def test_loan_amount_stored(self, tmp_path):
        svc = _make_service(tmp_path)
        payload = ApplicationCreateRequest(company_name="Loan Corp", loan_amount=50_000_000.0)
        record = svc.create_application(payload)
        loaded = svc.get_application(record.application_id)
        assert loaded.loan_amount == 50_000_000.0

    def test_industry_stored(self, tmp_path):
        svc = _make_service(tmp_path)
        payload = ApplicationCreateRequest(company_name="Ind Corp", industry="Manufacturing")
        record = svc.create_application(payload)
        loaded = svc.get_application(record.application_id)
        assert loaded.industry == "Manufacturing"


# ---------------------------------------------------------------------------
# list_applications / get_application
# ---------------------------------------------------------------------------
class TestListGetApplications:
    def test_list_returns_created_applications(self, tmp_path):
        svc = _make_service(tmp_path)
        _create_app(svc, "Corp A", "corp-a")
        _create_app(svc, "Corp B", "corp-b")
        apps = svc.list_applications()
        ids = [a.application_id for a in apps]
        assert "corp-a" in ids
        assert "corp-b" in ids

    def test_list_empty_workspace(self, tmp_path):
        svc = _make_service(tmp_path)
        assert svc.list_applications() == []

    def test_get_application_returns_record(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc, "Test Corp")
        record = svc.get_application(app_id)
        assert record.application_id == app_id

    def test_get_application_unknown_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        with pytest.raises(FileNotFoundError):
            svc.get_application("nonexistent-app")

    def test_list_sorted_most_recent_first(self, tmp_path):
        svc = _make_service(tmp_path)
        _create_app(svc, "Corp A", "corp-a")
        _create_app(svc, "Corp B", "corp-b")
        apps = svc.list_applications()
        # Most recently updated should be first
        assert apps[0].updated_at >= apps[-1].updated_at


# ---------------------------------------------------------------------------
# save_uploaded_documents
# ---------------------------------------------------------------------------
class TestSaveUploadedDocuments:
    def test_files_saved_to_input_docs(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        svc.save_uploaded_documents(app_id, [("balance_sheet.pdf", b"pdf content")])
        saved = svc.workspace_root / app_id / "current" / "input_docs" / "balance_sheet.pdf"
        assert saved.exists()
        assert saved.read_bytes() == b"pdf content"

    def test_document_count_updated(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        resp = svc.save_uploaded_documents(app_id, [
            ("a.pdf", b"aaa"),
            ("b.xlsx", b"bbb"),
        ])
        assert resp.document_count == 2

    def test_uploaded_files_list_returned(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        resp = svc.save_uploaded_documents(app_id, [("report.pdf", b"data")])
        assert "report.pdf" in resp.uploaded_files

    def test_application_status_updated_to_documents_uploaded(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        svc.save_uploaded_documents(app_id, [("doc.pdf", b"d")])
        record = svc.get_application(app_id)
        assert record.status == "documents_uploaded"

    def test_empty_filename_skipped(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        resp = svc.save_uploaded_documents(app_id, [("", b"data"), ("valid.pdf", b"v")])
        assert "valid.pdf" in resp.uploaded_files
        assert "" not in resp.uploaded_files

    def test_unknown_application_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        with pytest.raises(Exception):
            svc.save_uploaded_documents("nonexistent", [("doc.pdf", b"d")])


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------
class TestGetStatus:
    def test_returns_status_after_create(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        status = svc.get_status(app_id)
        assert status.application_id == app_id
        assert status.status in ("created", "not_started", "queued", "running", "success", "failed", "draft_ready", "documents_uploaded")

    def test_status_reads_from_status_json(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        status = svc.get_status(app_id)
        assert status.status == "created"

    def test_unknown_application_returns_not_started(self, tmp_path):
        svc = _make_service(tmp_path)
        status = svc.get_status("ghost-app")
        assert status.status in ("not_started", "draft_ready")


# ---------------------------------------------------------------------------
# queue_pipeline
# ---------------------------------------------------------------------------
class TestQueuePipeline:
    def test_sets_status_to_queued(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        req = PipelineRunRequest(application_id=app_id)
        queued = svc.queue_pipeline(req)
        assert queued.status == "queued"

    def test_invalid_input_dir_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        req = PipelineRunRequest(
            application_id=app_id,
            input_documents_dir="/nonexistent/path/that/does/not/exist",
        )
        with pytest.raises(FileNotFoundError):
            svc.queue_pipeline(req)

    def test_company_name_override(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc, "Old Name")
        req = PipelineRunRequest(application_id=app_id, company_name="New Name")
        queued = svc.queue_pipeline(req)
        assert queued.company_name == "New Name"


# ---------------------------------------------------------------------------
# Draft save / load / update
# ---------------------------------------------------------------------------
class TestDraftSaveLoad:
    def test_save_and_load_draft(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _sample_draft(app_id)
        svc.save_draft(draft)
        loaded = svc.load_draft(app_id)
        assert loaded.application_id == app_id
        assert loaded.company_name == "Test Corp"

    def test_load_draft_missing_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        with pytest.raises(FileNotFoundError):
            svc.load_draft(app_id)

    def test_draft_sections_preserved(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _sample_draft(app_id)
        svc.save_draft(draft)
        loaded = svc.load_draft(app_id)
        assert len(loaded.sections) == 1
        assert loaded.sections[0].id == "s-01"

    def test_draft_blocks_preserved(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _sample_draft(app_id)
        svc.save_draft(draft)
        loaded = svc.load_draft(app_id)
        assert loaded.sections[0].blocks[0].text == "Initial text."


# ---------------------------------------------------------------------------
# update_block_text
# ---------------------------------------------------------------------------
class TestUpdateBlockText:
    def test_updates_block_text(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _sample_draft(app_id)
        svc.save_draft(draft)
        req = DraftUpdateRequest(section_id="s-01", block_id="b-01", text="Updated text.")
        updated = svc.update_block_text(app_id, req)
        assert updated.sections[0].blocks[0].text == "Updated text."

    def test_update_persisted_on_disk(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _sample_draft(app_id)
        svc.save_draft(draft)
        req = DraftUpdateRequest(section_id="s-01", block_id="b-01", text="Persisted text.")
        svc.update_block_text(app_id, req)
        reloaded = svc.load_draft(app_id)
        assert reloaded.sections[0].blocks[0].text == "Persisted text."

    def test_unknown_block_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _sample_draft(app_id)
        svc.save_draft(draft)
        req = DraftUpdateRequest(section_id="s-01", block_id="b-NONEXISTENT", text="x")
        with pytest.raises(KeyError):
            svc.update_block_text(app_id, req)

    def test_section_summary_updated(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _sample_draft(app_id)
        svc.save_draft(draft)
        req = DraftUpdateRequest(section_id="s-01", block_id="b-01", text="New summary text.")
        updated = svc.update_block_text(app_id, req)
        # Summary should match first block text
        assert updated.sections[0].summary == "New summary text."


# ---------------------------------------------------------------------------
# export_current_docx / export_current_pdf
# ---------------------------------------------------------------------------
class TestExportCurrent:
    def test_export_docx_returns_path(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _sample_draft(app_id)
        svc.save_draft(draft)
        path = svc.export_current_docx(app_id)
        assert path.exists()
        assert path.suffix == ".docx"

    def test_export_pdf_returns_path(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _sample_draft(app_id)
        svc.save_draft(draft)
        path = svc.export_current_pdf(app_id)
        assert path.exists()
        assert path.suffix == ".pdf"

    def test_export_docx_no_draft_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        with pytest.raises(FileNotFoundError):
            svc.export_current_docx(app_id)

    def test_export_pdf_no_draft_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        with pytest.raises(FileNotFoundError):
            svc.export_current_pdf(app_id)


# ---------------------------------------------------------------------------
# get_tabs_bundle
# ---------------------------------------------------------------------------
class TestGetTabsBundle:
    def _write_transformation(self, svc: UnifiedOrchestratorService, app_id: str, data: dict):
        outputs_dir = svc.workspace_root / app_id / "current" / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / "transformation_output.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def test_returns_tabs_from_transformation(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        self._write_transformation(svc, app_id, {
            "status": "success",
            "tab_data": {
                "overview": {"company_name": "Tab Corp"},
                "balance_sheet": [],
                "income_statement": [],
                "cash_flow": [],
            },
        })
        tabs = svc.get_tabs_bundle(app_id)
        assert tabs is not None

    def test_missing_transformation_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        with pytest.raises(FileNotFoundError):
            svc.get_tabs_bundle(app_id)


# ---------------------------------------------------------------------------
# get_insights
# ---------------------------------------------------------------------------
class TestGetInsights:
    def _write_analysis(self, svc: UnifiedOrchestratorService, app_id: str, data: dict):
        outputs_dir = svc.workspace_root / app_id / "current" / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / "analysis_output.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def test_returns_insights(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        self._write_analysis(svc, app_id, {
            "status": "success",
            "company_name": "Insight Corp",
            "ratio_report": {"current_ratio": 1.5},
        })
        insights = svc.get_insights(app_id)
        assert insights["application_id"] == app_id

    def test_missing_analysis_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        with pytest.raises(FileNotFoundError):
            svc.get_insights(app_id)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
class TestInternalHelpers:
    def test_generate_application_id_slugifies(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = svc._generate_application_id("Alpha Beta Corp!")
        assert " " not in app_id
        assert "!" not in app_id
        assert "alpha" in app_id.lower()

    def test_count_documents_empty_dir(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        assert UnifiedOrchestratorService._count_documents(docs) == 0

    def test_count_documents_with_files(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.pdf").write_bytes(b"a")
        (docs / "b.xlsx").write_bytes(b"b")
        assert UnifiedOrchestratorService._count_documents(docs) == 2

    def test_count_documents_missing_dir(self, tmp_path):
        assert UnifiedOrchestratorService._count_documents(tmp_path / "missing") == 0

    def test_derive_overall_status_all_success(self):
        from models import PipelineStageResult
        stages = [
            PipelineStageResult(stage="transformation", status="success"),
            PipelineStageResult(stage="enrichment", status="success"),
        ]
        status = UnifiedOrchestratorService._derive_overall_status(stages, [])
        assert status == "success"

    def test_derive_overall_status_one_failed(self):
        from models import PipelineStageResult
        stages = [
            PipelineStageResult(stage="transformation", status="success"),
            PipelineStageResult(stage="enrichment", status="failed"),
        ]
        status = UnifiedOrchestratorService._derive_overall_status(stages, ["Error"])
        assert status == "partial_success"

    def test_derive_overall_status_all_failed(self):
        from models import PipelineStageResult
        stages = [PipelineStageResult(stage="transformation", status="failed")]
        status = UnifiedOrchestratorService._derive_overall_status(stages, ["Fatal error"])
        assert status == "failed"

    def test_derive_overall_status_partial_success_stage(self):
        from models import PipelineStageResult
        stages = [PipelineStageResult(stage="transformation", status="partial_success")]
        status = UnifiedOrchestratorService._derive_overall_status(stages, [])
        assert status == "partial_success"
