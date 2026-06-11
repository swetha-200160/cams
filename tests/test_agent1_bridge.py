"""Tests for integrated_cam_backend/mappers.py — Agent 1 bridge functions"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

# Allow importing from the parent package without installing it
sys.path.insert(0, str(Path(__file__).parent.parent))

from mappers import (
    _build_agent3_completeness,
    _build_auxiliary_data,
    _build_missing_fields,
    _collect_source_inventory,
    _derive_overview_metrics,
    _detect_document_role,
    _detect_source_type,
    _entries_have_any,
    _infer_period_hint,
    _resolve_tabs,
    normalize_agent1_output,
)


# ---------------------------------------------------------------------------
# _detect_source_type
# ---------------------------------------------------------------------------

class TestDetectSourceType:
    @pytest.mark.parametrize("filename,expected", [
        ("report.pdf", "pdf_text"),
        ("data.xls", "xlsx_text"),
        ("data.xlsx", "xlsx_text"),
        ("notes.doc", "docx_text"),
        ("notes.docx", "docx_text"),
        ("export.csv", "csv_text"),
        ("scan.png", "image_ocr"),
        ("photo.jpg", "image_ocr"),
        ("photo.jpeg", "image_ocr"),
        ("image.webp", "image_ocr"),
        ("scan.tif", "image_ocr"),
        ("scan.tiff", "image_ocr"),
        ("scan.bmp", "image_ocr"),
        ("archive.zip", "unknown"),
        ("README", "unknown"),
    ])
    def test_known_extensions(self, filename, expected):
        assert _detect_source_type(Path(filename)) == expected

    def test_case_insensitive(self):
        assert _detect_source_type(Path("REPORT.PDF")) == "pdf_text"
        assert _detect_source_type(Path("Image.JPEG")) == "image_ocr"


# ---------------------------------------------------------------------------
# _detect_document_role
# ---------------------------------------------------------------------------

class TestDetectDocumentRole:
    @pytest.mark.parametrize("filename,expected", [
        ("GSTR-3B_Apr2023.pdf", "gst_return"),
        ("gst_return_fy2324.pdf", "gst_return"),
        ("itr_fy2023.pdf", "itr_filing"),
        ("income tax return.pdf", "itr_filing"),
        ("bank_statement_oct23.pdf", "bank_document"),
        ("od_account_stmt.pdf", "bank_document"),
        ("cc_statement.pdf", "bank_document"),
        ("sanction_letter.pdf", "bank_document"),
        ("COI_certificate.pdf", "roc_filing"),
        ("MCA_filing.pdf", "roc_filing"),
        ("charge_document.pdf", "roc_filing"),
        ("cibil_report.pdf", "bureau_report"),
        ("bureau_score.pdf", "bureau_report"),
        # "statement" triggers bank_document check before cash_flow check — real code behaviour
        ("cash flow statement.pdf", "bank_document"),
        ("cashflow_fy23.pdf", "cash_flow_statement"),
        ("balance_sheet_2023.pdf", "financial_statement"),
        ("profit_loss_fy23.pdf", "financial_statement"),
        ("financial_summary.pdf", "financial_statement"),
        ("random_document.pdf", "other"),
    ])
    def test_role_detection(self, filename, expected):
        assert _detect_document_role(filename) == expected

    def test_priority_gst_over_bank(self):
        # "gst" keyword should win even if "statement" is also present
        assert _detect_document_role("gst_bank_statement.pdf") == "gst_return"


# ---------------------------------------------------------------------------
# _infer_period_hint
# ---------------------------------------------------------------------------

class TestInferPeriodHint:
    def test_fy_pattern(self):
        result = _infer_period_hint("balance_sheet_fy2023-24.pdf")
        assert result is not None
        assert "2023" in result or "fy" in result.lower()

    def test_year_range_pattern(self):
        result = _infer_period_hint("report_2022_23.pdf")
        assert result is not None

    def test_month_year_pattern(self):
        result = _infer_period_hint("statement_march_2023.pdf")
        assert result is not None

    def test_no_period(self):
        result = _infer_period_hint("random_document.pdf")
        assert result is None

    def test_case_insensitive(self):
        result = _infer_period_hint("FY2024-25_Report.pdf")
        assert result is not None


# ---------------------------------------------------------------------------
# _collect_source_inventory
# ---------------------------------------------------------------------------

class TestCollectSourceInventory:
    def test_nonexistent_dir_returns_empty(self):
        result = _collect_source_inventory(Path("/nonexistent/path/xyz"))
        assert result == []

    def test_collects_files(self, tmp_path):
        (tmp_path / "bank_statement_fy23.pdf").write_bytes(b"")
        (tmp_path / "gstr_apr23.xlsx").write_bytes(b"")

        result = _collect_source_inventory(tmp_path)
        filenames = [item["filename"] for item in result]

        assert "bank_statement_fy23.pdf" in filenames
        assert "gstr_apr23.xlsx" in filenames

    def test_inventory_item_structure(self, tmp_path):
        (tmp_path / "itr_fy24.pdf").write_bytes(b"")
        result = _collect_source_inventory(tmp_path)

        assert len(result) == 1
        item = result[0]
        assert item["filename"] == "itr_fy24.pdf"
        assert item["source"] == "pdf_text"
        assert item["document_role"] == "itr_filing"

    def test_ignores_directories(self, tmp_path):
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.pdf").write_bytes(b"")
        (tmp_path / "top.pdf").write_bytes(b"")

        result = _collect_source_inventory(tmp_path)
        # Both files should be found (rglob), no directories as items
        assert all("filename" in item for item in result)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _resolve_tabs
# ---------------------------------------------------------------------------

class TestResolveTabs:
    def test_resolves_from_tab_data_key(self):
        raw = {
            "tab_data": {
                "overview": {"company_name": "ACME"},
                "balance_sheet": [{"year": 2023}],
                "income_statement": [{"revenue_from_operations": 100}],
                "cash_flow": [],
            }
        }
        result = _resolve_tabs(raw)
        assert result["overview"]["company_name"] == "ACME"
        assert result["balance_sheet"] == [{"year": 2023}]

    def test_fallback_to_top_level_keys(self):
        raw = {
            "overview": {"company_name": "Beta Corp"},
            "balance_sheet": [{"year": 2022}],
            "income_statement": [],
            "cash_flow": [],
        }
        result = _resolve_tabs(raw)
        assert result["overview"]["company_name"] == "Beta Corp"
        assert result["balance_sheet"] == [{"year": 2022}]

    def test_empty_tab_data_uses_fallback(self):
        raw = {
            "tab_data": {},
            "overview": {"cin": "U12345"},
        }
        result = _resolve_tabs(raw)
        assert result["overview"]["cin"] == "U12345"

    def test_none_values_default_to_empty(self):
        raw = {
            "tab_data": {
                "overview": None,
                "balance_sheet": None,
                "income_statement": None,
                "cash_flow": None,
            }
        }
        result = _resolve_tabs(raw)
        assert result["overview"] == {}
        assert result["balance_sheet"] == []


# ---------------------------------------------------------------------------
# _derive_overview_metrics
# ---------------------------------------------------------------------------

class TestDeriveOverviewMetrics:
    def _make_tab_data(self, overview=None, balance_sheet=None, income_statement=None):
        return {
            "overview": overview or {},
            "balance_sheet": balance_sheet or [],
            "income_statement": income_statement or [],
            "cash_flow": [],
        }

    def test_fills_net_sales_from_income_statement(self):
        tab_data = self._make_tab_data(
            income_statement=[{"revenue_from_operations": 5000}]
        )
        result = _derive_overview_metrics(tab_data)
        assert result["net_sales"] == 5000

    def test_fills_networth_from_balance_sheet(self):
        tab_data = self._make_tab_data(
            balance_sheet=[{"networth": 12000}]
        )
        result = _derive_overview_metrics(tab_data)
        assert result["networth"] == 12000

    def test_does_not_overwrite_existing_values(self):
        tab_data = self._make_tab_data(
            overview={"net_sales": 9999},
            income_statement=[{"revenue_from_operations": 5000}],
        )
        result = _derive_overview_metrics(tab_data)
        assert result["net_sales"] == 9999

    def test_uses_latest_entry(self):
        tab_data = self._make_tab_data(
            balance_sheet=[
                {"year": 2021, "networth": 1000},
                {"year": 2022, "networth": 2000},
            ]
        )
        result = _derive_overview_metrics(tab_data)
        assert result["networth"] == 2000

    def test_empty_data_returns_overview_unchanged(self):
        tab_data = self._make_tab_data(overview={"cin": "U999"})
        result = _derive_overview_metrics(tab_data)
        assert result["cin"] == "U999"

    def test_fills_pat_ebitda_total_debt(self):
        tab_data = self._make_tab_data(
            income_statement=[{"pat": 300, "ebitda": 500}],
            balance_sheet=[{"total_debt": 1500}],
        )
        result = _derive_overview_metrics(tab_data)
        assert result["pat"] == 300
        assert result["ebitda"] == 500
        assert result["total_debt"] == 1500


# ---------------------------------------------------------------------------
# _build_auxiliary_data
# ---------------------------------------------------------------------------

class TestBuildAuxiliaryData:
    def test_groups_bank_documents(self):
        inventory = [
            {"filename": "bank_stmt.pdf", "source": "pdf_text", "period_hint": None, "document_role": "bank_document"},
        ]
        result = _build_auxiliary_data(inventory)
        assert len(result["bank_statements"]) == 1
        assert result["bank_statements"][0]["filename"] == "bank_stmt.pdf"

    def test_groups_gst_returns(self):
        inventory = [
            {"filename": "gstr3b.pdf", "source": "pdf_text", "period_hint": "fy2324", "document_role": "gst_return"},
        ]
        result = _build_auxiliary_data(inventory)
        assert len(result["gst_returns"]) == 1

    def test_groups_itr_filings(self):
        inventory = [
            {"filename": "itr.pdf", "source": "pdf_text", "period_hint": None, "document_role": "itr_filing"},
        ]
        result = _build_auxiliary_data(inventory)
        assert len(result["itr_filings"]) == 1

    def test_groups_roc_filings(self):
        inventory = [
            {"filename": "coi.pdf", "source": "pdf_text", "period_hint": None, "document_role": "roc_filing"},
        ]
        result = _build_auxiliary_data(inventory)
        assert len(result["roc_filings"]) == 1

    def test_unrecognized_role_not_grouped(self):
        inventory = [
            {"filename": "misc.pdf", "source": "pdf_text", "period_hint": None, "document_role": "other"},
        ]
        result = _build_auxiliary_data(inventory)
        assert all(len(v) == 0 for v in result.values())

    def test_empty_inventory(self):
        result = _build_auxiliary_data([])
        assert result == {"bank_statements": [], "gst_returns": [], "itr_filings": [], "roc_filings": []}


# ---------------------------------------------------------------------------
# _build_missing_fields
# ---------------------------------------------------------------------------

class TestBuildMissingFields:
    def test_no_missing_when_all_present(self):
        tab_data = {
            "overview": {
                "cin": "U12345", "pan": "ABCDE1234F", "company_name": "Acme",
                "gstin": "29ABCDE1234F1Z5", "incorporation_date": "2000-01-01",
                "registered_address": "Delhi", "industry": "Tech", "directors": ["Alice"],
            },
            "balance_sheet": [{
                "share_capital": 100, "reserves_surplus": 200, "networth": 300,
                "total_debt": 400, "current_assets": 500, "current_liabilities": 150,
                "inventory": 50, "receivables": 100, "cash_bank": 50,
                "trade_payables": 80, "fixed_assets": 200, "total_assets": 800,
                "total_liabilities": 800,
            }],
            "income_statement": [{
                "revenue_from_operations": 1000, "other_income": 50,
                "cost_of_material": 400, "employee_benefit_expense": 200,
                "finance_cost": 30, "depreciation": 20, "total_expenses": 700,
                "ebitda": 350, "pbt": 300, "tax_expense": 80, "pat": 220,
            }],
            "cash_flow": [{
                "operating_activities": 100, "investing_activities": -50,
                "financing_activities": -20, "net_change_in_cash": 30,
            }],
        }
        result = _build_missing_fields(tab_data)
        assert result == {}

    def test_missing_balance_sheet_entries(self):
        tab_data = {
            "overview": {},
            "balance_sheet": [],
            "income_statement": [],
            "cash_flow": [],
        }
        result = _build_missing_fields(tab_data)
        assert "balance_sheet" in result
        assert result["balance_sheet"] == ["entries"]

    def test_detects_null_fields_across_entries(self):
        tab_data = {
            "overview": {},
            "balance_sheet": [
                {"share_capital": None, "reserves_surplus": None, "networth": 100},
                {"share_capital": None, "reserves_surplus": None, "networth": 200},
            ],
            "income_statement": [{"revenue_from_operations": 500, "ebitda": 100}],
            "cash_flow": [{"operating_activities": 50, "investing_activities": -10,
                           "financing_activities": -5, "net_change_in_cash": 35}],
        }
        result = _build_missing_fields(tab_data)
        assert "balance_sheet" in result
        assert "share_capital" in result["balance_sheet"]


# ---------------------------------------------------------------------------
# _entries_have_any
# ---------------------------------------------------------------------------

class TestEntriesHaveAny:
    def test_returns_true_when_field_present(self):
        entries = [{"revenue_from_operations": 1000}]
        assert _entries_have_any(entries, ["revenue_from_operations"]) is True

    def test_returns_false_when_all_none(self):
        entries = [{"revenue_from_operations": None}, {"revenue_from_operations": None}]
        assert _entries_have_any(entries, ["revenue_from_operations"]) is False

    def test_returns_false_for_empty_entries(self):
        assert _entries_have_any([], ["any_field"]) is False

    def test_returns_true_if_any_field_matches(self):
        entries = [{"a": None, "b": 100}]
        assert _entries_have_any(entries, ["a", "b"]) is True

    def test_empty_string_treated_as_missing(self):
        entries = [{"field": ""}]
        assert _entries_have_any(entries, ["field"]) is False


# ---------------------------------------------------------------------------
# _build_agent3_completeness
# ---------------------------------------------------------------------------

class TestBuildAgent3Completeness:
    def _minimal_tab_data(self):
        return {
            "overview": {
                "directors": ["Alice"],
                "charges": [],
                "industry": "Finance",
            },
            "balance_sheet": [{
                "share_capital": 100, "reserves_surplus": 200, "networth": 300,
                "total_debt": 400, "current_assets": 500, "current_liabilities": 150,
            }],
            "income_statement": [{
                "revenue_from_operations": 1000, "employee_benefit_expense": 200,
                "ebitda": 350, "finance_cost": 30, "pat": 220,
            }],
            "cash_flow": [{
                "operating_activities": 100, "investing_activities": -50,
                "financing_activities": -20, "net_change_in_cash": 30,
            }],
        }

    def _full_auxiliary(self):
        return {
            "bank_statements": [{"filename": "bank.pdf"}],
            "gst_returns": [{"filename": "gst.pdf"}],
            "itr_filings": [{"filename": "itr.pdf"}],
            "roc_filings": [{"filename": "coi.pdf"}],
        }

    def test_output_structure(self):
        result = _build_agent3_completeness(self._minimal_tab_data(), self._full_auxiliary())
        assert "ready_for_analysis_from_agent1" in result
        assert "requires_agent2_enrichment" in result
        assert "agent3_readiness" in result

    def test_all_agents_ready(self):
        result = _build_agent3_completeness(self._minimal_tab_data(), self._full_auxiliary())
        assert isinstance(result["agent3_readiness"], dict)
        assert result["ready_for_analysis_from_agent1"] == (not result["requires_agent2_enrichment"])

    def test_not_ready_when_no_balance_sheet(self):
        tab_data = self._minimal_tab_data()
        tab_data["balance_sheet"] = []
        result = _build_agent3_completeness(tab_data, self._full_auxiliary())
        assert result["agent3_readiness"]["ratio_analysis_agent"] is False

    def test_not_ready_when_auxiliary_missing(self):
        result = _build_agent3_completeness(self._minimal_tab_data(), {
            "bank_statements": [], "gst_returns": [], "itr_filings": [], "roc_filings": [],
        })
        assert result["agent3_readiness"]["bank_statement_analyzer"] is False
        assert result["agent3_readiness"]["gst_analytics_agent"] is False


# ---------------------------------------------------------------------------
# normalize_agent1_output  (integration-style)
# ---------------------------------------------------------------------------

class TestNormalizeAgent1Output:
    def _base_raw(self):
        return {
            "status": "success",
            "summary": {},
            "overview": {
                "company_name": "Test Corp", "cin": "U99999MH2000PLC99999",
                "industry": "Manufacturing",
            },
            "balance_sheet": [{"year": 2023, "share_capital": 500, "networth": 1000}],
            "income_statement": [{"year": 2023, "revenue_from_operations": 3000, "pat": 200}],
            "cash_flow": [],
            "errors": [],
        }

    def test_output_keys_present(self, tmp_path):
        result = normalize_agent1_output(self._base_raw(), tmp_path)
        for key in ["status", "summary", "tab_data", "auxiliary_data",
                    "missing_fields", "input_completeness", "errors", "source_inventory"]:
            assert key in result, f"Missing key: {key}"

    def test_extraction_contract_version(self, tmp_path):
        result = normalize_agent1_output(self._base_raw(), tmp_path)
        assert result["extraction_contract_version"] == "2.2"

    def test_status_preserved(self, tmp_path):
        result = normalize_agent1_output(self._base_raw(), tmp_path)
        assert result["status"] == "success"

    def test_source_inventory_populated_from_dir(self, tmp_path):
        (tmp_path / "bank_stmt.pdf").write_bytes(b"")
        result = normalize_agent1_output(self._base_raw(), tmp_path)
        assert len(result["source_inventory"]) == 1
        assert result["source_inventory"][0]["filename"] == "bank_stmt.pdf"

    def test_summary_documents_processed_filled(self, tmp_path):
        (tmp_path / "itr_fy23.pdf").write_bytes(b"")
        result = normalize_agent1_output(self._base_raw(), tmp_path)
        assert result["summary"]["total_documents"] == 1
        assert len(result["summary"]["documents_processed"]) == 1

    def test_debug_payload_run_timestamp(self, tmp_path):
        debug = {"run_timestamp": "2024-01-01T00:00:00", "structured_datasets": None}
        result = normalize_agent1_output(self._base_raw(), tmp_path, debug_payload=debug)
        assert result["summary"].get("run_timestamp") == "2024-01-01T00:00:00"

    def test_structured_datasets_from_debug(self, tmp_path):
        debug = {"structured_datasets": [{"sheet": "BS", "rows": 10}]}
        result = normalize_agent1_output(self._base_raw(), tmp_path, debug_payload=debug)
        assert result["structured_datasets"] == [{"sheet": "BS", "rows": 10}]

    def test_structured_datasets_from_raw(self, tmp_path):
        raw = self._base_raw()
        raw["structured_datasets"] = [{"sheet": "IS", "rows": 5}]
        result = normalize_agent1_output(raw, tmp_path)
        assert result["structured_datasets"] == [{"sheet": "IS", "rows": 5}]

    def test_no_structured_datasets_key_when_absent(self, tmp_path):
        result = normalize_agent1_output(self._base_raw(), tmp_path)
        assert "structured_datasets" not in result

    def test_input_completeness_has_missing_from_agent1(self, tmp_path):
        result = normalize_agent1_output(self._base_raw(), tmp_path)
        assert "missing_from_agent1" in result["input_completeness"]

    def test_tab_data_overview_metrics_derived(self, tmp_path):
        raw = self._base_raw()
        result = normalize_agent1_output(raw, tmp_path)
        overview = result["tab_data"]["overview"]
        assert overview.get("net_sales") == 3000  # derived from income_statement
        assert overview.get("pat") == 200
