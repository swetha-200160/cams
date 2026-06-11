"""Tests for integrated_cam_backend/mappers.py — Agent 3 mapper functions"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mappers import (
    _clean_none_list,
    _map_gst_returns,
    _map_itr_filings,
    map_enrich_output_to_agent3_payload,
)


# ---------------------------------------------------------------------------
# _clean_none_list
# ---------------------------------------------------------------------------
class TestCleanNoneList:
    def test_removes_none_values(self):
        assert _clean_none_list([1, None, 2, None]) == [1, 2]

    def test_empty_list(self):
        assert _clean_none_list([]) == []

    def test_none_input(self):
        assert _clean_none_list(None) == []

    def test_non_list_input(self):
        assert _clean_none_list("string") == []

    def test_all_none(self):
        assert _clean_none_list([None, None]) == []

    def test_no_none(self):
        assert _clean_none_list(["a", "b"]) == ["a", "b"]


# ---------------------------------------------------------------------------
# _map_itr_filings
# ---------------------------------------------------------------------------
class TestMapItrFilings:
    def test_rich_itr_data_returns_sentinel_wrapped(self):
        raw = {"itr_data": {"pan": "ABCDE1234F", "filings": []}}
        result = _map_itr_filings(raw)
        assert len(result) == 1
        assert result[0].get("__rich_itr_data__") is True
        assert result[0]["pan"] == "ABCDE1234F"

    def test_falls_back_to_legacy_list(self):
        raw = {"itr_filings": [{"ay": "2023-24"}, {"ay": "2022-23"}]}
        result = _map_itr_filings(raw)
        assert len(result) == 2
        assert result[0]["ay"] == "2023-24"

    def test_empty_raw_returns_empty(self):
        result = _map_itr_filings({})
        assert result == []

    def test_itr_data_as_non_dict_falls_back_to_list(self):
        # itr_data must be a dict to trigger sentinel path
        raw = {"itr_data": ["not a dict"], "itr_filings": [{"ay": "2023"}]}
        result = _map_itr_filings(raw)
        # Non-dict itr_data → fall back to itr_filings list
        assert result == [{"ay": "2023"}]


# ---------------------------------------------------------------------------
# _map_gst_returns
# ---------------------------------------------------------------------------
class TestMapGstReturns:
    def test_rich_gst_data_returns_sentinel_wrapped(self):
        raw = {"gst_data": {"gstin": "27ABC", "returns": []}}
        result = _map_gst_returns(raw)
        assert len(result) == 1
        assert result[0].get("__rich_gst_data__") is True
        assert result[0]["gstin"] == "27ABC"

    def test_falls_back_to_legacy_gst_returns(self):
        raw = {"gst_returns": [{"period": "2023-24", "gst_paid": 50000}]}
        result = _map_gst_returns(raw)
        assert result[0]["period"] == "2023-24"

    def test_empty_raw_returns_empty(self):
        assert _map_gst_returns({}) == []

    def test_gst_data_as_non_dict_falls_back(self):
        raw = {"gst_data": "invalid", "gst_returns": [{"period": "2023"}]}
        result = _map_gst_returns(raw)
        assert result == [{"period": "2023"}]


# ---------------------------------------------------------------------------
# map_enrich_output_to_agent3_payload
# ---------------------------------------------------------------------------
class TestMapEnrichOutputToAgent3Payload:
    def _sample_enrich_output(self):
        return {
            "status": "success",
            "enriched_tabs": {
                "overview": {
                    "cin": "L12345MH",
                    "pan": "ABCDE1234F",
                    "company_name": "ACME Ltd",
                    "industry": "Manufacturing",
                    "gstin": "27ABCDE",
                    "incorporation_date": "2005-01-01",
                    "address": "Mumbai",
                    "directors": ["Alice", None, "Bob"],
                    "charges": [],
                    "legal_cases": [],
                },
                "balance_sheet": [
                    {
                        "year": "FY2022",
                        "equity": 300,
                        "long_term_debt": 200,
                        "short_term_borrowing": 100,
                        "fixed_assets": 400,
                        "source_document": "bs.pdf",
                    },
                    {
                        "year": "FY2023",
                        "equity": 350,
                        "long_term_debt": 180,
                        "short_term_borrowing": 120,
                        "fixed_assets": 420,
                        "source_document": "bs.pdf",
                    },
                ],
                "income_statement": [
                    {"year": "FY2022", "revenue": 4000, "ebitda": 600, "pat": 300},
                    {"year": "FY2023", "revenue": 5000, "ebitda": 800, "pat": 420, "interest_expense": 150},
                ],
                "cash_flow": [
                    {
                        "year": "FY2023",
                        "operating_cash_flow": 500,
                        "investing_cash_flow": -200,
                        "financing_cash_flow": -100,
                        "net_cash_flow": 200,
                    }
                ],
            },
            "summary": {
                "run_timestamp": "2024-01-01T00:00:00Z",
                "fields_scraped": 10,
                "fields_flagged": 2,
                "sources_used": ["mca"],
                "errors": [],
            },
            "retrieved_fields": [],
            "flagged_manual": [],
            "raw_scraped_data": {
                "bank_statements": [{"month": "Jan", "balance": 10000}],
                "roc_filings": [{"year": "2023"}],
            },
        }

    def test_status_passed_through(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        assert result["status"] == "success"

    def test_enriched_overview_company_name(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        assert result["enriched_overview"]["company_name"] == "ACME Ltd"

    def test_enriched_overview_net_sales_from_latest_income(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        # Latest income statement is FY2023 with revenue=5000
        assert result["enriched_overview"]["net_sales"] == 5000

    def test_enriched_overview_networth_from_latest_bs(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        # Latest balance sheet FY2023 has equity=350
        assert result["enriched_overview"]["networth"] == 350

    def test_total_debt_computed_from_lt_plus_st(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        # FY2023: long_term_debt=180 + short_term_borrowing=120 = 300
        assert result["enriched_overview"]["total_debt"] == 300

    def test_total_debt_from_explicit_total_debt(self):
        enrich = self._sample_enrich_output()
        enrich["enriched_tabs"]["balance_sheet"][-1]["total_debt"] = 999
        result = map_enrich_output_to_agent3_payload(enrich)
        assert result["enriched_overview"]["total_debt"] == 999

    def test_directors_cleaned_of_none(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        # ["Alice", None, "Bob"] → ["Alice", "Bob"]
        assert result["enriched_overview"]["directors"] == ["Alice", "Bob"]

    def test_balance_sheet_mapped(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        assert len(result["balance_sheet"]) == 2
        assert result["balance_sheet"][1]["year"] == "FY2023"
        assert result["balance_sheet"][1]["long_term_borrowing"] == 180

    def test_balance_sheet_long_term_debt_alias(self):
        enrich = self._sample_enrich_output()
        enrich["enriched_tabs"]["balance_sheet"][0]["long_term_borrowing"] = 555
        del enrich["enriched_tabs"]["balance_sheet"][0]["long_term_debt"]
        result = map_enrich_output_to_agent3_payload(enrich)
        assert result["balance_sheet"][0]["long_term_borrowing"] == 555

    def test_income_statement_mapped(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        assert len(result["income_statement"]) == 2
        assert result["income_statement"][1]["revenue_from_operations"] == 5000

    def test_income_statement_finance_cost_alias(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        # FY2023 has interest_expense=150
        assert result["income_statement"][1]["finance_cost"] == 150

    def test_cash_flow_mapped(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        assert len(result["cash_flow"]) == 1
        assert result["cash_flow"][0]["cash_from_operating_activities"] == 500
        assert result["cash_flow"][0]["net_change_in_cash"] == 200

    def test_bank_statements_from_raw_scraped(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        assert result["bank_statements"] == [{"month": "Jan", "balance": 10000}]

    def test_roc_filings_from_raw_scraped(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        assert result["roc_filings"] == [{"year": "2023"}]

    def test_gst_returns_rich_format(self):
        enrich = self._sample_enrich_output()
        enrich["raw_scraped_data"]["gst_data"] = {"gstin": "27ABC", "returns": []}
        result = map_enrich_output_to_agent3_payload(enrich)
        assert result["gst_returns"][0]["__rich_gst_data__"] is True

    def test_itr_filings_rich_format(self):
        enrich = self._sample_enrich_output()
        enrich["raw_scraped_data"]["itr_data"] = {"pan": "ABCDE1234F"}
        result = map_enrich_output_to_agent3_payload(enrich)
        assert result["itr_filings"][0]["__rich_itr_data__"] is True

    def test_empty_enrich_output_does_not_crash(self):
        result = map_enrich_output_to_agent3_payload({})
        assert "enriched_overview" in result
        assert result["balance_sheet"] == []
        assert result["income_statement"] == []
        assert result["cash_flow"] == []

    def test_summary_mapped(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        assert result["summary"]["fields_retrieved"] == 10
        assert result["summary"]["fields_flagged"] == 2
        assert result["summary"]["sources_used"] == ["mca"]

    def test_missing_enriched_tabs_uses_empty(self):
        result = map_enrich_output_to_agent3_payload({"status": "failed"})
        assert result["enriched_overview"]["company_name"] is None
        assert result["enriched_overview"]["net_sales"] is None

    def test_result_has_required_top_level_keys(self):
        result = map_enrich_output_to_agent3_payload(self._sample_enrich_output())
        for key in (
            "status", "summary", "enriched_overview",
            "retrieved_fields", "flagged_fields",
            "balance_sheet", "income_statement", "cash_flow",
            "bank_statements", "gst_returns", "itr_filings", "roc_filings",
        ):
            assert key in result, f"Missing key: {key}"
