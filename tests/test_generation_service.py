"""Tests for integrated_cam_backend/generation_service.py"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from generation_service import (
    CamDraftGenerator,
    _effective_tabs,
    _money,
    _pct,
    _string,
    _tab_data,
)
from models import CamDraft


# ---------------------------------------------------------------------------
# Pure formatting helpers
# ---------------------------------------------------------------------------
class TestMoneyFormatter:
    def test_formats_number(self):
        assert _money(5000000) == "₹5,000,000.00"

    def test_zero(self):
        assert _money(0) == "₹0.00"

    def test_none_returns_not_available(self):
        assert _money(None) == "not available"

    def test_empty_string_returns_not_available(self):
        assert _money("") == "not available"

    def test_non_numeric_string_returns_as_is(self):
        result = _money("N/A")
        assert result == "N/A"

    def test_float_value(self):
        assert _money(1234.56) == "₹1,234.56"


class TestPctFormatter:
    def test_converts_fraction_to_percent(self):
        assert _pct(0.25) == "25.00%"

    def test_none_returns_not_available(self):
        assert _pct(None) == "not available"

    def test_empty_string_returns_not_available(self):
        assert _pct("") == "not available"

    def test_zero(self):
        assert _pct(0) == "0.00%"

    def test_non_numeric_returns_as_is(self):
        result = _pct("N/A")
        assert result == "N/A"


class TestStringFormatter:
    def test_returns_string(self):
        assert _string("hello") == "hello"

    def test_none_returns_default(self):
        assert _string(None) == "not available"

    def test_empty_string_returns_default(self):
        assert _string("") == "not available"

    def test_empty_list_returns_default(self):
        assert _string([]) == "not available"

    def test_empty_dict_returns_default(self):
        assert _string({}) == "not available"

    def test_custom_default(self):
        assert _string(None, default="unknown") == "unknown"

    def test_number_converted_to_string(self):
        assert _string(42) == "42"


# ---------------------------------------------------------------------------
# _tab_data
# ---------------------------------------------------------------------------
class TestTabData:
    def test_resolves_tab_data_wrapper(self):
        transformation = {
            "tab_data": {
                "overview": {"company_name": "ACME"},
                "balance_sheet": [{"year": "FY2023"}],
                "income_statement": [],
                "cash_flow": [],
            }
        }
        result = _tab_data(transformation)
        assert result["overview"]["company_name"] == "ACME"

    def test_falls_back_to_root(self):
        transformation = {
            "overview": {"company_name": "Root"},
            "balance_sheet": [],
            "income_statement": [],
            "cash_flow": [],
        }
        result = _tab_data(transformation)
        assert result["overview"]["company_name"] == "Root"

    def test_empty_tab_data_falls_back(self):
        result = _tab_data({"tab_data": {}, "overview": {"company_name": "FB"}})
        assert result["overview"]["company_name"] == "FB"


# ---------------------------------------------------------------------------
# _effective_tabs
# ---------------------------------------------------------------------------
class TestEffectiveTabs:
    def test_prefers_enriched_tabs(self):
        transformation = {"tab_data": {"overview": {"company_name": "Agent1"}}}
        enrichment = {"enriched_tabs": {"overview": {"company_name": "Enriched"}, "balance_sheet": [], "income_statement": [], "cash_flow": []}}
        result = _effective_tabs(transformation, enrichment)
        assert result["overview"]["company_name"] == "Enriched"

    def test_falls_back_to_transformation(self):
        transformation = {"tab_data": {"overview": {"company_name": "Agent1"}, "balance_sheet": [], "income_statement": [], "cash_flow": []}}
        enrichment = {}
        result = _effective_tabs(transformation, enrichment)
        assert result["overview"]["company_name"] == "Agent1"

    def test_empty_enriched_tabs_falls_back(self):
        transformation = {"tab_data": {"overview": {"company_name": "Agent1"}, "balance_sheet": [], "income_statement": [], "cash_flow": []}}
        enrichment = {"enriched_tabs": {}}  # empty → falsy
        result = _effective_tabs(transformation, enrichment)
        assert result["overview"]["company_name"] == "Agent1"


# ---------------------------------------------------------------------------
# CamDraftGenerator.generate — integration tests using empty agent outputs
# ---------------------------------------------------------------------------
def _make_generator(tmp_path: Path, app_id: str = "app-test") -> CamDraftGenerator:
    """Create a generator with a real (empty) input_docs_dir so SourceLocatorService works."""
    docs_dir = tmp_path / "input_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    return CamDraftGenerator(application_id=app_id, input_docs_dir=docs_dir)


def _minimal_transformation() -> dict:
    return {
        "status": "success",
        "tab_data": {
            "overview": {"company_name": "Test Corp", "industry": "IT"},
            "balance_sheet": [],
            "income_statement": [],
            "cash_flow": [],
        },
        "summary": {"documents_processed": []},
    }


def _minimal_enrichment() -> dict:
    return {
        "status": "success",
        "enriched_tabs": {
            "overview": {
                "company_name": "Test Corp",
                "industry": "IT",
                "cin": "L12345MH",
                "gstin": "27ABC",
                "directors": ["Alice"],
            },
            "balance_sheet": [],
            "income_statement": [],
            "cash_flow": [],
        },
    }


def _minimal_analysis() -> dict:
    return {
        "status": "partial",
        "company_name": "Test Corp",
        "ratio_report": {},
        "trend_report": {},
        "banking_behaviour": {},
        "cash_flow_projection": {},
        "gst_analytics": {},
        "tax_compliance": {},
        "related_party": {},
        "industry_intelligence": {},
        "market_risk": {},
        "parsed_financials": {},
    }


class TestCamDraftGeneratorGenerate:
    def test_returns_cam_draft_instance(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        assert isinstance(draft, CamDraft)

    def test_application_id_set(self, tmp_path):
        gen = _make_generator(tmp_path, app_id="my-app-01")
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        assert draft.application_id == "my-app-01"

    def test_company_name_used(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Explicit Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        assert draft.company_name == "Explicit Corp"

    def test_company_name_falls_back_to_analysis(self, tmp_path):
        gen = _make_generator(tmp_path)
        analysis = _minimal_analysis()
        analysis["company_name"] = "From Analysis Corp"
        draft = gen.generate(None, {}, {}, analysis)
        assert draft.company_name == "From Analysis Corp"

    def test_company_name_falls_back_to_unknown(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate(None, {}, {}, {})
        assert draft.company_name == "Unknown Borrower"

    def test_generates_multiple_sections(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        assert len(draft.sections) > 5

    def test_executive_summary_section_present(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        ids = [s.id for s in draft.sections]
        assert "executive_summary" in ids

    def test_borrower_profile_section_present(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        ids = [s.id for s in draft.sections]
        assert "borrower_profile" in ids

    def test_financial_analysis_section_present(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        ids = [s.id for s in draft.sections]
        assert "financial_statement_analysis" in ids

    def test_each_section_has_at_least_one_block(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        for section in draft.sections:
            assert len(section.blocks) >= 1, f"Section {section.id} has no blocks"

    def test_each_block_has_non_empty_text(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        for section in draft.sections:
            for block in section.blocks:
                assert block.text, f"Block {block.id} in section {section.id} has empty text"

    def test_each_section_has_valid_status(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        valid = {"ready", "partial", "pending"}
        for section in draft.sections:
            assert section.status in valid, f"Invalid status '{section.status}' in section {section.id}"

    def test_generated_at_is_set(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        assert draft.generated_at is not None

    def test_notes_always_non_empty(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        assert len(draft.notes) >= 1

    def test_no_analysis_adds_note(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), {})
        note_text = " ".join(draft.notes)
        assert "Agent 3" in note_text

    def test_financial_section_shows_revenue(self, tmp_path):
        transformation = _minimal_transformation()
        transformation["tab_data"]["income_statement"] = [
            {"year": "FY2023", "revenue": 9999}
        ]
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", transformation, _minimal_enrichment(), _minimal_analysis())
        fin_section = next(s for s in draft.sections if s.id == "executive_summary")
        block_text = " ".join(b.text for b in fin_section.blocks)
        # Revenue 9999 should appear formatted
        assert "9,999" in block_text or "9999" in block_text

    def test_enriched_tabs_prioritised_in_borrower_profile(self, tmp_path):
        enrichment = _minimal_enrichment()
        enrichment["enriched_tabs"]["overview"]["company_name"] = "Enriched Corp"
        gen = _make_generator(tmp_path)
        draft = gen.generate(None, _minimal_transformation(), enrichment, _minimal_analysis())
        borrower = next(s for s in draft.sections if s.id == "borrower_profile")
        assert any("Enriched Corp" in b.text for b in borrower.blocks)

    def test_promoter_section_lists_directors(self, tmp_path):
        enrichment = _minimal_enrichment()
        enrichment["enriched_tabs"]["overview"]["directors"] = ["Alice Smith", "Bob Jones"]
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), enrichment, _minimal_analysis())
        promoter = next(s for s in draft.sections if s.id == "promoter_profile")
        block_text = " ".join(b.text for b in promoter.blocks)
        assert "Alice Smith" in block_text
        assert "Bob Jones" in block_text

    def test_all_section_ids_unique(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        ids = [s.id for s in draft.sections]
        assert len(ids) == len(set(ids)), "Duplicate section IDs detected"

    def test_all_block_ids_unique_within_section(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate("Test Corp", _minimal_transformation(), _minimal_enrichment(), _minimal_analysis())
        for section in draft.sections:
            block_ids = [b.id for b in section.blocks]
            assert len(block_ids) == len(set(block_ids)), f"Duplicate block IDs in section {section.id}"

    def test_empty_all_inputs_does_not_crash(self, tmp_path):
        gen = _make_generator(tmp_path)
        draft = gen.generate(None, {}, {}, {})
        assert isinstance(draft, CamDraft)
        assert draft.company_name == "Unknown Borrower"
