"""Tests for integrated_cam_backend/mappers.py — Agent 2 mapper functions"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mappers import (
    _map_balance_sheet,
    _map_cash_flow,
    _map_income_statement,
    _map_overview,
    _resolve_tab_data,
    map_transformation_output_to_agent2_payload,
)


# ---------------------------------------------------------------------------
# _resolve_tab_data
# ---------------------------------------------------------------------------
class TestResolveTabData:
    def test_resolves_tab_data_wrapper(self):
        transformation = {
            "tab_data": {
                "overview": {"company_name": "ACME"},
                "balance_sheet": [{"year": "FY2023"}],
                "income_statement": [],
                "cash_flow": [],
            }
        }
        result = _resolve_tab_data(transformation)
        assert result["overview"]["company_name"] == "ACME"
        assert result["balance_sheet"][0]["year"] == "FY2023"

    def test_falls_back_to_root_keys(self):
        transformation = {
            "overview": {"company_name": "Fallback Corp"},
            "balance_sheet": [{"year": "FY2022"}],
            "income_statement": [],
            "cash_flow": [],
        }
        result = _resolve_tab_data(transformation)
        assert result["overview"]["company_name"] == "Fallback Corp"

    def test_empty_tab_data_falls_back(self):
        transformation = {
            "tab_data": {},  # falsy dict → fallback
            "overview": {"company_name": "Root Corp"},
        }
        result = _resolve_tab_data(transformation)
        assert result["overview"]["company_name"] == "Root Corp"

    def test_none_tab_data_falls_back(self):
        transformation = {
            "tab_data": None,
            "overview": {"company_name": "Root2"},
        }
        result = _resolve_tab_data(transformation)
        assert result["overview"]["company_name"] == "Root2"

    def test_missing_sub_keys_return_empty(self):
        transformation = {"tab_data": {"overview": {"company_name": "Co"}}}
        result = _resolve_tab_data(transformation)
        assert result["balance_sheet"] == []
        assert result["income_statement"] == []
        assert result["cash_flow"] == []

    def test_null_sub_keys_normalised_to_empty(self):
        transformation = {
            "tab_data": {
                "overview": None,
                "balance_sheet": None,
            }
        }
        result = _resolve_tab_data(transformation)
        assert result["overview"] == {}
        assert result["balance_sheet"] == []


# ---------------------------------------------------------------------------
# _map_overview
# ---------------------------------------------------------------------------
class TestMapOverview:
    def test_maps_standard_fields(self):
        overview = {
            "company_name": "Test Corp",
            "industry": "IT",
            "cin": "L12345MH2000PLC123456",
            "pan": "ABCDE1234F",
            "gstin": "27ABCDE1234F1Z5",
            "address": "Mumbai",
            "directors": ["Alice", "Bob"],
            "incorporation_date": "2000-01-01",
        }
        result = _map_overview(overview)
        assert result["company_name"] == "Test Corp"
        assert result["cin"] == "L12345MH2000PLC123456"
        assert result["directors"] == ["Alice", "Bob"]

    def test_falls_back_to_registered_address(self):
        result = _map_overview({"registered_address": "Delhi"})
        assert result["address"] == "Delhi"

    def test_address_takes_priority_over_registered(self):
        result = _map_overview({"address": "Mumbai", "registered_address": "Delhi"})
        assert result["address"] == "Mumbai"

    def test_missing_fields_are_none(self):
        result = _map_overview({})
        assert result["company_name"] is None
        assert result["cin"] is None
        assert result["directors"] == []

    def test_charges_and_legal_cases_default_empty(self):
        result = _map_overview({})
        assert result["charges"] == []
        assert result["legal_cases"] == []


# ---------------------------------------------------------------------------
# _map_balance_sheet
# ---------------------------------------------------------------------------
class TestMapBalanceSheet:
    def test_maps_standard_fields(self):
        rows = [
            {
                "year": "FY2023",
                "total_assets": 1000,
                "total_liabilities": 600,
                "equity": 400,
                "current_assets": 300,
                "current_liabilities": 200,
                "fixed_assets": 700,
                "long_term_debt": 400,
                "cash_and_equivalents": 50,
                "source_document": "bs.pdf",
            }
        ]
        result = _map_balance_sheet(rows)
        assert len(result) == 1
        assert result[0]["year"] == "FY2023"
        assert result[0]["total_assets"] == 1000
        assert result[0]["equity"] == 400

    def test_equity_falls_back_to_networth(self):
        rows = [{"year": "FY2023", "networth": 300}]
        result = _map_balance_sheet(rows)
        assert result[0]["equity"] == 300

    def test_equity_falls_back_to_reserves_surplus(self):
        rows = [{"year": "FY2023", "reserves_surplus": 250}]
        result = _map_balance_sheet(rows)
        assert result[0]["equity"] == 250

    def test_long_term_debt_alias(self):
        rows = [{"year": "FY2023", "long_term_borrowing": 500}]
        result = _map_balance_sheet(rows)
        assert result[0]["long_term_debt"] == 500

    def test_cash_alias_cash_and_bank(self):
        rows = [{"year": "FY2023", "cash_and_bank": 75}]
        result = _map_balance_sheet(rows)
        assert result[0]["cash_and_equivalents"] == 75

    def test_cash_alias_cash_bank(self):
        rows = [{"year": "FY2023", "cash_bank": 60}]
        result = _map_balance_sheet(rows)
        assert result[0]["cash_and_equivalents"] == 60

    def test_empty_list(self):
        assert _map_balance_sheet([]) == []

    def test_none_returns_empty(self):
        assert _map_balance_sheet(None) == []

    def test_multiple_years(self):
        rows = [{"year": "FY2022"}, {"year": "FY2023"}]
        result = _map_balance_sheet(rows)
        assert len(result) == 2
        assert result[1]["year"] == "FY2023"


# ---------------------------------------------------------------------------
# _map_income_statement
# ---------------------------------------------------------------------------
class TestMapIncomeStatement:
    def test_maps_standard_fields(self):
        rows = [
            {
                "year": "FY2023",
                "revenue": 5000,
                "ebitda": 800,
                "pat": 400,
                "depreciation": 100,
                "interest_expense": 150,
                "tax": 50,
            }
        ]
        result = _map_income_statement(rows)
        assert result[0]["revenue"] == 5000
        assert result[0]["pat"] == 400

    def test_revenue_alias_revenue_from_operations(self):
        rows = [{"year": "FY2023", "revenue_from_operations": 4500}]
        result = _map_income_statement(rows)
        assert result[0]["revenue"] == 4500

    def test_revenue_alias_net_sales(self):
        rows = [{"year": "FY2023", "net_sales": 4200}]
        result = _map_income_statement(rows)
        assert result[0]["revenue"] == 4200

    def test_operating_expenses_alias_total_expenses(self):
        rows = [{"year": "FY2023", "total_expenses": 3000}]
        result = _map_income_statement(rows)
        assert result[0]["operating_expenses"] == 3000

    def test_interest_expense_alias_finance_cost(self):
        rows = [{"year": "FY2023", "finance_cost": 200}]
        result = _map_income_statement(rows)
        assert result[0]["interest_expense"] == 200

    def test_tax_alias_tax_expense(self):
        rows = [{"year": "FY2023", "tax_expense": 80}]
        result = _map_income_statement(rows)
        assert result[0]["tax"] == 80

    def test_empty_list(self):
        assert _map_income_statement([]) == []

    def test_none_returns_empty(self):
        assert _map_income_statement(None) == []


# ---------------------------------------------------------------------------
# _map_cash_flow
# ---------------------------------------------------------------------------
class TestMapCashFlow:
    def test_maps_standard_fields(self):
        rows = [
            {
                "year": "FY2023",
                "operating_cash_flow": 600,
                "investing_cash_flow": -200,
                "financing_cash_flow": -100,
                "net_cash_flow": 300,
                "capital_expenditure": 250,
            }
        ]
        result = _map_cash_flow(rows)
        assert result[0]["operating_cash_flow"] == 600
        assert result[0]["net_cash_flow"] == 300

    def test_operating_alias_operating_activities(self):
        rows = [{"year": "FY2023", "operating_activities": 500}]
        result = _map_cash_flow(rows)
        assert result[0]["operating_cash_flow"] == 500

    def test_operating_alias_cash_from_operating(self):
        rows = [{"year": "FY2023", "cash_from_operating_activities": 450}]
        result = _map_cash_flow(rows)
        assert result[0]["operating_cash_flow"] == 450

    def test_net_cash_flow_alias_net_change_in_cash(self):
        rows = [{"year": "FY2023", "net_change_in_cash": 200}]
        result = _map_cash_flow(rows)
        assert result[0]["net_cash_flow"] == 200

    def test_empty_list(self):
        assert _map_cash_flow([]) == []

    def test_none_returns_empty(self):
        assert _map_cash_flow(None) == []


# ---------------------------------------------------------------------------
# map_transformation_output_to_agent2_payload (integration)
# ---------------------------------------------------------------------------
class TestMapTransformationOutputToAgent2Payload:
    def _write_json(self, tmp_path: Path, data: dict) -> Path:
        p = tmp_path / "transformation_output.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_basic_success_payload(self, tmp_path):
        data = {
            "status": "success",
            "tab_data": {
                "overview": {"company_name": "ACME"},
                "balance_sheet": [{"year": "FY2023", "total_assets": 1000}],
                "income_statement": [{"year": "FY2023", "revenue": 5000}],
                "cash_flow": [],
            },
            "summary": {
                "run_timestamp": "2024-01-01T00:00:00Z",
                "total_documents": 2,
                "documents_processed": ["bs.pdf", "pl.xlsx"],
                "tabs_populated": {},
                "error_count": 0,
            },
        }
        path = self._write_json(tmp_path, data)
        result = map_transformation_output_to_agent2_payload(path)

        assert result["status"] == "success"
        assert result["tab_data"]["overview"]["company_name"] == "ACME"
        assert result["tab_data"]["balance_sheet"][0]["total_assets"] == 1000
        assert result["tab_data"]["income_statement"][0]["revenue"] == 5000

    def test_failed_status_preserved(self, tmp_path):
        data = {"status": "failed", "errors": ["Agent failed"]}
        path = self._write_json(tmp_path, data)
        result = map_transformation_output_to_agent2_payload(path)
        assert result["status"] == "failed"

    def test_bank_transactions_merged_into_auxiliary(self, tmp_path):
        txns = [{"date": "2024-01-01", "amount": 5000, "type": "credit"}]
        data = {
            "status": "success",
            "bank_statements": {"transactions": txns},
        }
        path = self._write_json(tmp_path, data)
        result = map_transformation_output_to_agent2_payload(path)
        assert result["auxiliary_data"]["bank_statements"] == txns

    def test_gst_data_merged_into_auxiliary(self, tmp_path):
        gst = {"gstin": "27ABC", "filings": []}
        data = {"status": "success", "gst_data": gst}
        path = self._write_json(tmp_path, data)
        result = map_transformation_output_to_agent2_payload(path)
        assert result["auxiliary_data"]["gst_data"] == gst

    def test_itr_data_merged_into_auxiliary(self, tmp_path):
        itr = {"pan": "ABCDE1234F", "filings": []}
        data = {"status": "success", "itr_data": itr}
        path = self._write_json(tmp_path, data)
        result = map_transformation_output_to_agent2_payload(path)
        assert result["auxiliary_data"]["itr_data"] == itr

    def test_missing_fields_passed_through(self, tmp_path):
        data = {
            "status": "partial_success",
            "missing_fields": {"balance_sheet": ["long_term_debt"]},
        }
        path = self._write_json(tmp_path, data)
        result = map_transformation_output_to_agent2_payload(path)
        assert result["missing_fields"]["balance_sheet"] == ["long_term_debt"]

    def test_errors_passed_through(self, tmp_path):
        data = {"status": "partial_success", "errors": ["Page 3 unreadable"]}
        path = self._write_json(tmp_path, data)
        result = map_transformation_output_to_agent2_payload(path)
        assert "Page 3 unreadable" in result["errors"]

    def test_tabs_populated_summary_derived(self, tmp_path):
        data = {
            "status": "success",
            "tab_data": {
                "overview": {"company_name": "Co"},
                "balance_sheet": [{"year": "FY2023"}],
                "income_statement": [{"year": "FY2023"}],
                "cash_flow": [],
            },
        }
        path = self._write_json(tmp_path, data)
        result = map_transformation_output_to_agent2_payload(path)
        summary = result["summary"]
        assert summary["tabs_populated"]["balance_sheet"] == 1
        assert summary["tabs_populated"]["income_statement"] == 1

    def test_result_has_required_keys(self, tmp_path):
        data = {"status": "success"}
        path = self._write_json(tmp_path, data)
        result = map_transformation_output_to_agent2_payload(path)
        for key in ("status", "summary", "tab_data", "auxiliary_data", "missing_fields", "errors"):
            assert key in result
