"""Tests for write_draft_outputs and render_draft_markdown in generation_service.py"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import CamBlock, CamDraft, CamSection, EvidenceReference, FileLocator
from generation_service import render_draft_markdown, write_draft_outputs

SAMPLE_DOCS_DIR = Path(r"C:\Users\abdula\Downloads\transformation_agent 1\transformation_agent\input_docs")
sample_docs_available = pytest.mark.skipif(
    not SAMPLE_DOCS_DIR.exists(),
    reason=f"Sample docs not found at {SAMPLE_DOCS_DIR}",
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _citation(cid: str = "c-01", doc: str = "report.pdf", label: str = "Page 1") -> EvidenceReference:
    return EvidenceReference(
        id=cid,
        document_name=doc,
        locator=FileLocator(type="page", page=1, label=label),
        extracted_value=100.0,
        source_field="revenue",
        source_year="FY24",
    )


def _simple_draft(app_id: str = "app-001") -> CamDraft:
    block = CamBlock(id="b-1", title="Revenue Overview", text="Revenue was ₹100.00 in FY24.")
    section = CamSection(id="s-1", title="Executive Summary", blocks=[block], status="ready")
    return CamDraft(
        application_id=app_id,
        company_name="Acme Ltd",
        generated_at=_now(),
        sections=[section],
    )


def _draft_with_citations(app_id: str = "app-002") -> CamDraft:
    cite = _citation("c-01", "PL_FY24.pdf", "Page 5")
    block = CamBlock(id="b-1", title="Financials", text="Net profit: ₹500 Cr.", citations=[cite])
    section = CamSection(id="s-1", title="Financial Analysis", blocks=[block], status="ready", page_hint="Page 4-6")
    return CamDraft(
        application_id=app_id,
        company_name="Test Bank Borrower",
        generated_at=_now(),
        sections=[section],
        notes=["Pending ITR verification", "Auditor comments required"],
    )


# ---------------------------------------------------------------------------
# render_draft_markdown
# ---------------------------------------------------------------------------

class TestRenderDraftMarkdown:
    def test_starts_with_h1_company_name(self):
        draft = _simple_draft()
        md = render_draft_markdown(draft)
        assert md.startswith("# Draft CAM - Acme Ltd")

    def test_includes_generated_at(self):
        draft = _simple_draft()
        md = render_draft_markdown(draft)
        assert "Generated at:" in md

    def test_section_title_is_h2(self):
        draft = _simple_draft()
        md = render_draft_markdown(draft)
        assert "## Executive Summary" in md

    def test_block_title_is_h3(self):
        draft = _simple_draft()
        md = render_draft_markdown(draft)
        assert "### Revenue Overview" in md

    def test_block_text_is_present(self):
        draft = _simple_draft()
        md = render_draft_markdown(draft)
        assert "Revenue was ₹100.00 in FY24." in md

    def test_no_citations_no_evidence_line(self):
        draft = _simple_draft()
        md = render_draft_markdown(draft)
        assert "Evidence:" not in md

    def test_citation_appears_in_evidence_line(self):
        draft = _draft_with_citations()
        md = render_draft_markdown(draft)
        assert "Evidence:" in md
        assert "PL_FY24.pdf" in md

    def test_citation_locator_label_appears(self):
        draft = _draft_with_citations()
        md = render_draft_markdown(draft)
        assert "Page 5" in md

    def test_page_hint_appears_in_output(self):
        draft = _draft_with_citations()
        md = render_draft_markdown(draft)
        assert "Page 4-6" in md

    def test_notes_section_present_when_notes_given(self):
        draft = _draft_with_citations()
        md = render_draft_markdown(draft)
        assert "## Draft Notes" in md
        assert "Pending ITR verification" in md
        assert "Auditor comments required" in md

    def test_no_notes_section_when_empty(self):
        draft = _simple_draft()
        md = render_draft_markdown(draft)
        assert "## Draft Notes" not in md

    def test_multiple_sections_all_present(self):
        s1 = CamSection(id="s-1", title="Section One", blocks=[CamBlock(id="b-1", title="B1", text="T1")], status="ready")
        s2 = CamSection(id="s-2", title="Section Two", blocks=[CamBlock(id="b-2", title="B2", text="T2")], status="ready")
        draft = CamDraft(
            application_id="app-x",
            company_name="Multi Corp",
            generated_at=_now(),
            sections=[s1, s2],
        )
        md = render_draft_markdown(draft)
        assert "## Section One" in md
        assert "## Section Two" in md
        assert "T1" in md
        assert "T2" in md

    def test_multiple_citations_joined_by_semicolon(self):
        cite1 = _citation("c-1", "doc_a.pdf", "Page 1")
        cite2 = _citation("c-2", "doc_b.pdf", "Page 2")
        block = CamBlock(id="b-1", title="Block", text="Text.", citations=[cite1, cite2])
        section = CamSection(id="s-1", title="Section", blocks=[block], status="ready")
        draft = CamDraft(
            application_id="app-multi",
            company_name="Corp",
            generated_at=_now(),
            sections=[section],
        )
        md = render_draft_markdown(draft)
        evidence_line = next(line for line in md.splitlines() if line.startswith("Evidence:"))
        assert ";" in evidence_line
        assert "doc_a.pdf" in evidence_line
        assert "doc_b.pdf" in evidence_line

    def test_returns_string(self):
        draft = _simple_draft()
        assert isinstance(render_draft_markdown(draft), str)

    def test_empty_sections_list(self):
        draft = CamDraft(
            application_id="empty-app",
            company_name="Empty Corp",
            generated_at=_now(),
            sections=[],
        )
        md = render_draft_markdown(draft)
        assert "# Draft CAM - Empty Corp" in md

    def test_citation_with_no_locator_label(self):
        cite = EvidenceReference(
            id="c-x",
            document_name="nodoc.pdf",
            locator=FileLocator(type="file", label=None),
        )
        block = CamBlock(id="b-1", title="B", text="T", citations=[cite])
        section = CamSection(id="s-1", title="S", blocks=[block], status="ready")
        draft = CamDraft(
            application_id="app-y",
            company_name="Corp Y",
            generated_at=_now(),
            sections=[section],
        )
        md = render_draft_markdown(draft)
        # Should not crash, and doc name should appear
        assert "nodoc.pdf" in md


# ---------------------------------------------------------------------------
# write_draft_outputs
# ---------------------------------------------------------------------------

class TestWriteDraftOutputs:
    def test_creates_both_files(self, tmp_path):
        draft = _simple_draft()
        json_path, md_path = write_draft_outputs(draft, tmp_path)
        assert json_path.exists()
        assert md_path.exists()

    def test_json_filename(self, tmp_path):
        draft = _simple_draft()
        json_path, _ = write_draft_outputs(draft, tmp_path)
        assert json_path.name == "cam_draft.json"

    def test_md_filename(self, tmp_path):
        draft = _simple_draft()
        _, md_path = write_draft_outputs(draft, tmp_path)
        assert md_path.name == "cam_draft.md"

    def test_json_is_valid(self, tmp_path):
        draft = _simple_draft()
        json_path, _ = write_draft_outputs(draft, tmp_path)
        parsed = json.loads(json_path.read_text(encoding="utf-8"))
        assert isinstance(parsed, dict)

    def test_json_contains_application_id(self, tmp_path):
        draft = _simple_draft("my-app-id")
        json_path, _ = write_draft_outputs(draft, tmp_path)
        parsed = json.loads(json_path.read_text(encoding="utf-8"))
        assert parsed["application_id"] == "my-app-id"

    def test_json_contains_company_name(self, tmp_path):
        draft = _simple_draft()
        json_path, _ = write_draft_outputs(draft, tmp_path)
        parsed = json.loads(json_path.read_text(encoding="utf-8"))
        assert parsed["company_name"] == "Acme Ltd"

    def test_json_contains_sections(self, tmp_path):
        draft = _simple_draft()
        json_path, _ = write_draft_outputs(draft, tmp_path)
        parsed = json.loads(json_path.read_text(encoding="utf-8"))
        assert len(parsed["sections"]) == 1
        assert parsed["sections"][0]["title"] == "Executive Summary"

    def test_md_contains_company_name(self, tmp_path):
        draft = _simple_draft()
        _, md_path = write_draft_outputs(draft, tmp_path)
        content = md_path.read_text(encoding="utf-8")
        assert "Acme Ltd" in content

    def test_md_contains_block_text(self, tmp_path):
        draft = _simple_draft()
        _, md_path = write_draft_outputs(draft, tmp_path)
        content = md_path.read_text(encoding="utf-8")
        assert "Revenue was ₹100.00 in FY24." in content

    def test_creates_output_dir_if_missing(self, tmp_path):
        output_dir = tmp_path / "nested" / "cam"
        assert not output_dir.exists()
        draft = _simple_draft()
        write_draft_outputs(draft, output_dir)
        assert output_dir.exists()

    def test_overwrites_existing_files(self, tmp_path):
        draft1 = _simple_draft()
        write_draft_outputs(draft1, tmp_path)

        draft2 = CamDraft(
            application_id="app-002",
            company_name="New Corp",
            generated_at=_now(),
            sections=[],
        )
        json_path, _ = write_draft_outputs(draft2, tmp_path)
        parsed = json.loads(json_path.read_text(encoding="utf-8"))
        assert parsed["company_name"] == "New Corp"

    def test_citations_serialized_in_json(self, tmp_path):
        draft = _draft_with_citations()
        json_path, _ = write_draft_outputs(draft, tmp_path)
        parsed = json.loads(json_path.read_text(encoding="utf-8"))
        block_data = parsed["sections"][0]["blocks"][0]
        assert len(block_data["citations"]) == 1
        assert block_data["citations"][0]["document_name"] == "PL_FY24.pdf"

    def test_notes_serialized_in_json(self, tmp_path):
        draft = _draft_with_citations()
        json_path, _ = write_draft_outputs(draft, tmp_path)
        parsed = json.loads(json_path.read_text(encoding="utf-8"))
        assert "Pending ITR verification" in parsed["notes"]

    def test_json_utf8_encoded(self, tmp_path):
        # Rupee symbol and non-ASCII must survive round-trip
        block = CamBlock(id="b-1", title="B", text="₹1,00,000 revenue")
        section = CamSection(id="s-1", title="S", blocks=[block], status="ready")
        draft = CamDraft(
            application_id="app-utf8",
            company_name="Naïve Corp",
            generated_at=_now(),
            sections=[section],
        )
        json_path, _ = write_draft_outputs(draft, tmp_path)
        raw = json_path.read_text(encoding="utf-8")
        assert "₹1,00,000" in raw
        assert "Naïve Corp" in raw

    def test_returns_correct_path_objects(self, tmp_path):
        draft = _simple_draft()
        json_path, md_path = write_draft_outputs(draft, tmp_path)
        assert isinstance(json_path, Path)
        assert isinstance(md_path, Path)
