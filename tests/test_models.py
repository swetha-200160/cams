"""Tests for integrated_cam_backend/models.py — Pydantic model validation."""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    ApplicationCreateRequest,
    ApplicationRecord,
    ArtifactPaths,
    CamBlock,
    CamDraft,
    CamSection,
    DocumentUploadResponse,
    DraftEvidenceUpdateRequest,
    DraftUpdateRequest,
    EvidenceReference,
    FileLocator,
    OrchestratorStatusResponse,
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineStageResult,
)


# ---------------------------------------------------------------------------
# FileLocator
# ---------------------------------------------------------------------------
class TestFileLocator:
    def test_default_type_is_unknown(self):
        loc = FileLocator()
        assert loc.type == "unknown"

    def test_page_locator(self):
        loc = FileLocator(type="page", page=3, label="Page 3")
        assert loc.type == "page"
        assert loc.page == 3
        assert loc.label == "Page 3"

    def test_sheet_cell_locator(self):
        loc = FileLocator(type="sheet_cell", sheet_name="Sheet1", cell="B5", row_number=5)
        assert loc.type == "sheet_cell"
        assert loc.sheet_name == "Sheet1"
        assert loc.cell == "B5"

    def test_paragraph_locator(self):
        loc = FileLocator(type="paragraph", paragraph_index=7)
        assert loc.paragraph_index == 7

    def test_all_optional_fields_default_none(self):
        loc = FileLocator()
        assert loc.page is None
        assert loc.sheet_name is None
        assert loc.cell is None
        assert loc.paragraph_index is None
        assert loc.label is None

    def test_invalid_type_raises(self):
        with pytest.raises(Exception):
            FileLocator(type="not_a_valid_type")


# ---------------------------------------------------------------------------
# EvidenceReference
# ---------------------------------------------------------------------------
class TestEvidenceReference:
    def test_basic_construction(self):
        ref = EvidenceReference(id="ev-001", document_name="balance_sheet.pdf")
        assert ref.id == "ev-001"
        assert ref.document_name == "balance_sheet.pdf"

    def test_default_locator_is_unknown(self):
        ref = EvidenceReference(id="ev-001", document_name="doc.pdf")
        assert ref.locator.type == "unknown"

    def test_custom_locator(self):
        loc = FileLocator(type="page", page=2)
        ref = EvidenceReference(
            id="ev-002",
            document_name="report.pdf",
            extracted_value=500000,
            source_field="revenue",
            source_year="FY2023",
            locator=loc,
        )
        assert ref.locator.page == 2
        assert ref.extracted_value == 500000

    def test_hyperlink_stored(self):
        ref = EvidenceReference(
            id="ev-003",
            document_name="doc.pdf",
            hyperlink="/api/files/app-01/doc.pdf",
        )
        assert ref.hyperlink == "/api/files/app-01/doc.pdf"


# ---------------------------------------------------------------------------
# CamBlock
# ---------------------------------------------------------------------------
class TestCamBlock:
    def test_basic_block(self):
        block = CamBlock(id="b-01", title="Revenue Overview", text="Revenue was ₹500 Cr.")
        assert block.id == "b-01"
        assert block.editable is True
        assert block.citations == []

    def test_block_with_citations(self):
        ref = EvidenceReference(id="ev-01", document_name="p_l.pdf")
        block = CamBlock(id="b-02", title="P&L", text="PAT is positive.", citations=[ref])
        assert len(block.citations) == 1
        assert block.citations[0].document_name == "p_l.pdf"


# ---------------------------------------------------------------------------
# CamSection
# ---------------------------------------------------------------------------
class TestCamSection:
    def test_default_status_pending(self):
        section = CamSection(id="s-01", title="Executive Summary")
        assert section.status == "pending"
        assert section.blocks == []

    def test_valid_statuses(self):
        for status in ("ready", "partial", "pending"):
            s = CamSection(id="s-01", title="T", status=status)
            assert s.status == status

    def test_invalid_status_raises(self):
        with pytest.raises(Exception):
            CamSection(id="s-01", title="T", status="in_progress")

    def test_section_with_blocks(self):
        block = CamBlock(id="b-01", title="Intro", text="Text here.")
        section = CamSection(id="s-01", title="Overview", blocks=[block], status="ready")
        assert len(section.blocks) == 1


# ---------------------------------------------------------------------------
# CamDraft
# ---------------------------------------------------------------------------
class TestCamDraft:
    def _now(self):
        return datetime.now(timezone.utc)

    def test_basic_draft(self):
        draft = CamDraft(
            application_id="app-01",
            company_name="ACME Pvt Ltd",
            generated_at=self._now(),
        )
        assert draft.sections == []
        assert draft.source_documents == []
        assert draft.notes == []

    def test_draft_with_sections(self):
        section = CamSection(id="s-01", title="Executive Summary")
        draft = CamDraft(
            application_id="app-01",
            company_name="ACME",
            generated_at=self._now(),
            sections=[section],
        )
        assert len(draft.sections) == 1

    def test_draft_serialises_and_round_trips(self):
        draft = CamDraft(
            application_id="app-01",
            company_name="Test Co",
            generated_at=self._now(),
            notes=["Draft note"],
        )
        data = draft.model_dump(mode="json")
        restored = CamDraft.model_validate(data)
        assert restored.application_id == "app-01"
        assert restored.notes == ["Draft note"]


# ---------------------------------------------------------------------------
# ApplicationCreateRequest
# ---------------------------------------------------------------------------
class TestApplicationCreateRequest:
    def test_minimal_request(self):
        req = ApplicationCreateRequest(company_name="Test Corp")
        assert req.company_name == "Test Corp"
        assert req.loan_amount is None
        assert req.application_id is None

    def test_full_request(self):
        req = ApplicationCreateRequest(
            application_id="test-corp-abc123",
            company_name="Test Corp",
            loan_amount=50000000.0,
            application_date=date(2024, 4, 1),
            industry="Manufacturing",
            loan_type="Term Loan",
        )
        assert req.loan_amount == 50000000.0
        assert req.industry == "Manufacturing"

    def test_missing_company_name_raises(self):
        with pytest.raises(Exception):
            ApplicationCreateRequest()


# ---------------------------------------------------------------------------
# ApplicationRecord
# ---------------------------------------------------------------------------
class TestApplicationRecord:
    def _now(self):
        return datetime.now(timezone.utc)

    def test_default_status_created(self):
        now = self._now()
        record = ApplicationRecord(
            application_id="app-01",
            company_name="Corp",
            created_at=now,
            updated_at=now,
        )
        assert record.status == "created"
        assert record.document_count == 0

    def test_round_trip_json(self):
        now = self._now()
        record = ApplicationRecord(
            application_id="app-01",
            company_name="Corp",
            status="queued",
            created_at=now,
            updated_at=now,
            document_count=3,
        )
        json_str = record.model_dump_json()
        restored = ApplicationRecord.model_validate_json(json_str)
        assert restored.application_id == "app-01"
        assert restored.document_count == 3
        assert restored.status == "queued"


# ---------------------------------------------------------------------------
# PipelineRunRequest
# ---------------------------------------------------------------------------
class TestPipelineRunRequest:
    def test_minimal_request(self):
        req = PipelineRunRequest(application_id="app-01")
        assert req.application_id == "app-01"
        assert req.generate_draft is True
        assert req.company_name is None
        assert req.input_documents_dir is None

    def test_missing_application_id_raises(self):
        with pytest.raises(Exception):
            PipelineRunRequest()

    def test_generate_draft_can_be_false(self):
        req = PipelineRunRequest(application_id="app-01", generate_draft=False)
        assert req.generate_draft is False


# ---------------------------------------------------------------------------
# PipelineStageResult
# ---------------------------------------------------------------------------
class TestPipelineStageResult:
    def test_transformation_stage(self):
        result = PipelineStageResult(stage="transformation", status="success")
        assert result.stage == "transformation"
        assert result.details == {}

    def test_invalid_stage_raises(self):
        with pytest.raises(Exception):
            PipelineStageResult(stage="unknown_stage", status="success")

    def test_invalid_status_raises(self):
        with pytest.raises(Exception):
            PipelineStageResult(stage="transformation", status="done")


# ---------------------------------------------------------------------------
# ArtifactPaths
# ---------------------------------------------------------------------------
class TestArtifactPaths:
    def test_all_none_by_default(self):
        arts = ArtifactPaths()
        assert arts.transformation_output is None
        assert arts.enrichment_output is None
        assert arts.analysis_output is None
        assert arts.cam_draft_output is None
        assert arts.cam_docx_output is None
        assert arts.cam_pdf_output is None

    def test_can_set_paths(self):
        arts = ArtifactPaths(
            transformation_output="/tmp/transformation_output.json",
            cam_draft_output="/tmp/cam_draft.json",
        )
        assert arts.transformation_output == "/tmp/transformation_output.json"


# ---------------------------------------------------------------------------
# DraftUpdateRequest / DraftEvidenceUpdateRequest
# ---------------------------------------------------------------------------
class TestDraftUpdateRequest:
    def test_valid_request(self):
        req = DraftUpdateRequest(section_id="s-01", block_id="b-01", text="Updated text")
        assert req.text == "Updated text"

    def test_missing_fields_raise(self):
        with pytest.raises(Exception):
            DraftUpdateRequest(section_id="s-01")


class TestDraftEvidenceUpdateRequest:
    def test_valid(self):
        req = DraftEvidenceUpdateRequest(
            section_id="s-01",
            block_id="b-01",
            citation_id="c-01",
            text="New excerpt",
        )
        assert req.citation_id == "c-01"


# ---------------------------------------------------------------------------
# OrchestratorStatusResponse
# ---------------------------------------------------------------------------
class TestOrchestratorStatusResponse:
    def test_minimal_valid(self):
        now = datetime.now(timezone.utc)
        resp = OrchestratorStatusResponse(
            application_id="app-01",
            status="created",
            last_updated_at=now,
        )
        assert resp.draft_available is False
        assert resp.stages == []
        assert resp.errors == []

    def test_round_trip(self):
        now = datetime.now(timezone.utc)
        resp = OrchestratorStatusResponse(
            application_id="app-01",
            status="queued",
            current_stage="transformation",
            last_updated_at=now,
        )
        json_str = resp.model_dump_json()
        restored = OrchestratorStatusResponse.model_validate_json(json_str)
        assert restored.status == "queued"
        assert restored.current_stage == "transformation"


# ---------------------------------------------------------------------------
# DocumentUploadResponse
# ---------------------------------------------------------------------------
class TestDocumentUploadResponse:
    def test_basic(self):
        resp = DocumentUploadResponse(
            application_id="app-01",
            uploaded_files=["bs.pdf", "pl.xlsx"],
            stored_in="/workspaces/app-01/current/input_docs",
            document_count=2,
        )
        assert resp.document_count == 2
        assert "bs.pdf" in resp.uploaded_files
