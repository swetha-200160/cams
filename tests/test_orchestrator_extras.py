"""Tests for get_evidence_payload and resolve_document_path in orchestrator_service.py"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    ApplicationCreateRequest,
    CamBlock,
    CamDraft,
    CamSection,
    EvidenceReference,
    FileLocator,
)
from orchestrator_service import UnifiedOrchestratorService

SAMPLE_DOCS_DIR = Path(r"C:\Users\abdula\Downloads\transformation_agent 1\transformation_agent\input_docs")
sample_docs_available = pytest.mark.skipif(
    not SAMPLE_DOCS_DIR.exists(),
    reason=f"Sample docs not found at {SAMPLE_DOCS_DIR}",
)


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


def _create_app(svc: UnifiedOrchestratorService, company: str = "Test Corp", app_id: str = "test-app") -> str:
    payload = ApplicationCreateRequest(company_name=company, application_id=app_id)
    record = svc.create_application(payload)
    return record.application_id


def _make_citation(cid: str = "c-01", doc: str = "report.pdf") -> EvidenceReference:
    return EvidenceReference(
        id=cid,
        document_name=doc,
        locator=FileLocator(type="page", page=3, label="Page 3"),
        extracted_value=42.5,
        source_field="revenue",
        source_year="FY24",
    )


def _make_draft_with_citation(app_id: str, section_id="s-01", block_id="b-01", citation_id="c-01", doc="report.pdf") -> CamDraft:
    citation = _make_citation(citation_id, doc)
    block = CamBlock(id=block_id, title="Revenue Block", text="Revenue is ₹42.50.", citations=[citation])
    section = CamSection(id=section_id, title="Executive Summary", blocks=[block], status="ready")
    return CamDraft(
        application_id=app_id,
        company_name="Test Corp",
        generated_at=_now(),
        sections=[section],
    )


# ---------------------------------------------------------------------------
# get_evidence_payload — happy path
# ---------------------------------------------------------------------------

class TestGetEvidencePayload:
    def test_returns_payload_with_correct_ids(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _make_draft_with_citation(app_id)
        svc.save_draft(draft)

        payload = svc.get_evidence_payload(app_id, "s-01", "b-01", "c-01")

        assert payload.application_id == app_id
        assert payload.section_id == "s-01"
        assert payload.block_id == "b-01"

    def test_citation_data_is_forwarded(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _make_draft_with_citation(app_id, citation_id="cite-99", doc="balance.xlsx")
        svc.save_draft(draft)

        payload = svc.get_evidence_payload(app_id, "s-01", "b-01", "cite-99")

        assert payload.citation.id == "cite-99"
        assert payload.citation.document_name == "balance.xlsx"

    def test_editable_text_matches_block_text(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _make_draft_with_citation(app_id)
        svc.save_draft(draft)

        payload = svc.get_evidence_payload(app_id, "s-01", "b-01", "c-01")
        assert payload.editable_text == "Revenue is ₹42.50."

    def test_preview_type_pdf(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _make_draft_with_citation(app_id, doc="annual_report.pdf")
        svc.save_draft(draft)

        payload = svc.get_evidence_payload(app_id, "s-01", "b-01", "c-01")
        assert payload.preview_type == "pdf"

    def test_preview_type_image(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _make_draft_with_citation(app_id, doc="scan.jpg")
        svc.save_draft(draft)

        payload = svc.get_evidence_payload(app_id, "s-01", "b-01", "c-01")
        assert payload.preview_type == "image"

    def test_preview_type_download_xlsx(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _make_draft_with_citation(app_id, doc="data.xlsx")
        svc.save_draft(draft)

        payload = svc.get_evidence_payload(app_id, "s-01", "b-01", "c-01")
        assert payload.preview_type == "download"

    def test_preview_type_unknown(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _make_draft_with_citation(app_id, doc="archive.zip")
        svc.save_draft(draft)

        payload = svc.get_evidence_payload(app_id, "s-01", "b-01", "c-01")
        assert payload.preview_type == "unknown"

    def test_source_file_url_is_set(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _make_draft_with_citation(app_id, doc="report.pdf")
        svc.save_draft(draft)

        payload = svc.get_evidence_payload(app_id, "s-01", "b-01", "c-01")
        assert payload.source_file_url == f"/api/files/{app_id}/report.pdf"


# ---------------------------------------------------------------------------
# get_evidence_payload — error paths
# ---------------------------------------------------------------------------

class TestGetEvidencePayloadErrors:
    def test_wrong_citation_id_raises_key_error(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _make_draft_with_citation(app_id, citation_id="c-01")
        svc.save_draft(draft)

        with pytest.raises(KeyError):
            svc.get_evidence_payload(app_id, "s-01", "b-01", "c-WRONG")

    def test_wrong_block_id_raises_key_error(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _make_draft_with_citation(app_id, block_id="b-01")
        svc.save_draft(draft)

        with pytest.raises(KeyError):
            svc.get_evidence_payload(app_id, "s-01", "b-WRONG", "c-01")

    def test_wrong_section_id_raises_key_error(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        draft = _make_draft_with_citation(app_id, section_id="s-01")
        svc.save_draft(draft)

        with pytest.raises(KeyError):
            svc.get_evidence_payload(app_id, "s-WRONG", "b-01", "c-01")

    def test_no_draft_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        # No draft saved

        with pytest.raises((FileNotFoundError, KeyError)):
            svc.get_evidence_payload(app_id, "s-01", "b-01", "c-01")

    def test_multiple_sections_finds_correct_one(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)

        cite1 = _make_citation("c-A", "doc_a.pdf")
        cite2 = _make_citation("c-B", "doc_b.pdf")
        block1 = CamBlock(id="b-1", title="Block 1", text="Text 1", citations=[cite1])
        block2 = CamBlock(id="b-2", title="Block 2", text="Text 2", citations=[cite2])
        sec1 = CamSection(id="s-1", title="Section 1", blocks=[block1], status="ready")
        sec2 = CamSection(id="s-2", title="Section 2", blocks=[block2], status="ready")
        draft = CamDraft(
            application_id=app_id,
            company_name="Test Corp",
            generated_at=_now(),
            sections=[sec1, sec2],
        )
        svc.save_draft(draft)

        payload = svc.get_evidence_payload(app_id, "s-2", "b-2", "c-B")
        assert payload.citation.document_name == "doc_b.pdf"
        assert payload.section_id == "s-2"


# ---------------------------------------------------------------------------
# resolve_document_path — happy path
# ---------------------------------------------------------------------------

class TestResolveDocumentPath:
    def test_exact_filename_match(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        workspace = svc._workspace(app_id)
        workspace.input_docs_dir.mkdir(parents=True, exist_ok=True)

        target = workspace.input_docs_dir / "annual_report.pdf"
        target.write_bytes(b"%PDF-1.4 fake")

        resolved = svc.resolve_document_path(app_id, "annual_report.pdf")
        assert resolved == target

    def test_case_insensitive_match(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        workspace = svc._workspace(app_id)
        workspace.input_docs_dir.mkdir(parents=True, exist_ok=True)

        target = workspace.input_docs_dir / "Annual_Report.PDF"
        target.write_bytes(b"%PDF-1.4 fake")

        resolved = svc.resolve_document_path(app_id, "annual_report.pdf")
        assert resolved.name.lower() == "annual_report.pdf"

    def test_unknown_document_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        workspace = svc._workspace(app_id)
        workspace.input_docs_dir.mkdir(parents=True, exist_ok=True)

        with pytest.raises(FileNotFoundError):
            svc.resolve_document_path(app_id, "does_not_exist.pdf")

    def test_file_in_subdirectory(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        workspace = svc._workspace(app_id)
        subdir = workspace.input_docs_dir / "financials"
        subdir.mkdir(parents=True, exist_ok=True)

        target = subdir / "balance_sheet.xlsx"
        target.write_bytes(b"PK fake xlsx")

        resolved = svc.resolve_document_path(app_id, "balance_sheet.xlsx")
        assert resolved == target

    def test_multiple_files_returns_exact_match_first(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        workspace = svc._workspace(app_id)
        workspace.input_docs_dir.mkdir(parents=True, exist_ok=True)
        subdir = workspace.input_docs_dir / "sub"
        subdir.mkdir(parents=True, exist_ok=True)

        exact = workspace.input_docs_dir / "target.pdf"
        exact.write_bytes(b"%PDF exact")
        other = subdir / "target.pdf"
        other.write_bytes(b"%PDF other")

        resolved = svc.resolve_document_path(app_id, "target.pdf")
        assert resolved == exact


# ---------------------------------------------------------------------------
# resolve_document_path — with real sample docs
# ---------------------------------------------------------------------------

@sample_docs_available
class TestResolveDocumentPathRealDocs:
    def test_resolves_real_pdf_files(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        workspace = svc._workspace(app_id)
        workspace.input_docs_dir.mkdir(parents=True, exist_ok=True)

        import shutil
        pdf_files = list(SAMPLE_DOCS_DIR.glob("*.pdf"))[:2]
        if not pdf_files:
            pytest.skip("No PDF files in sample docs directory")

        for src in pdf_files:
            shutil.copy(src, workspace.input_docs_dir / src.name)

        for pdf in pdf_files:
            resolved = svc.resolve_document_path(app_id, pdf.name)
            assert resolved.exists()
            assert resolved.suffix.lower() == ".pdf"

    def test_resolves_real_xlsx_files(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        workspace = svc._workspace(app_id)
        workspace.input_docs_dir.mkdir(parents=True, exist_ok=True)

        import shutil
        xlsx_files = list(SAMPLE_DOCS_DIR.glob("*.xlsx"))[:2]
        if not xlsx_files:
            pytest.skip("No XLSX files in sample docs directory")

        for src in xlsx_files:
            shutil.copy(src, workspace.input_docs_dir / src.name)

        for xlsx in xlsx_files:
            resolved = svc.resolve_document_path(app_id, xlsx.name)
            assert resolved.exists()
            assert resolved.suffix.lower() == ".xlsx"

    def test_missing_doc_raises_when_real_docs_present(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        workspace = svc._workspace(app_id)
        workspace.input_docs_dir.mkdir(parents=True, exist_ok=True)

        import shutil
        any_file = next(iter(SAMPLE_DOCS_DIR.iterdir()), None)
        if any_file and any_file.is_file():
            shutil.copy(any_file, workspace.input_docs_dir / any_file.name)

        with pytest.raises(FileNotFoundError):
            svc.resolve_document_path(app_id, "phantom_document_xyz.pdf")


# ---------------------------------------------------------------------------
# _preview_type static helper
# ---------------------------------------------------------------------------

class TestPreviewType:
    @pytest.mark.parametrize("name,expected", [
        ("report.pdf", "pdf"),
        ("REPORT.PDF", "pdf"),
        ("scan.jpg", "image"),
        ("photo.jpeg", "image"),
        ("diagram.png", "image"),
        ("preview.webp", "image"),
        ("data.xlsx", "download"),
        ("data.xls", "download"),
        ("doc.docx", "download"),
        ("notes.txt", "download"),
        ("export.csv", "download"),
        ("archive.zip", "unknown"),
        ("file.bin", "unknown"),
    ])
    def test_preview_type_classification(self, name, expected):
        result = UnifiedOrchestratorService._preview_type(name)
        assert result == expected
