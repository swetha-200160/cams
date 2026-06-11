"""
mappers.py — Inter-agent data transformation bridges.

Functions
---------
normalize_agent1_output                      Agent 1 raw output → normalised contract
map_transformation_output_to_agent2_payload  Agent 1 JSON file  → Agent 2 payload dict
map_enrich_output_to_agent3_payload          Agent 2 enrich output → Agent 3 input dict
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Shared constants ───────────────────────────────────────────────────────────

_META_FIELDS = {"source_document", "source_documents"}
_OVERVIEW_PRIORITY = {
    "cin", "pan", "company_name", "gstin",
    "incorporation_date", "registered_address", "industry", "directors",
}

AGENT3_REQUIRED_FIELDS: dict[str, dict[str, list[str]]] = {
    "financial_statement_parser": {
        "balance_sheet": ["share_capital", "reserves_surplus"],
        "income_statement": ["revenue_from_operations", "employee_benefit_expense"],
    },
    "ratio_analysis_agent": {
        "balance_sheet": ["total_debt", "networth", "current_assets", "current_liabilities"],
        "income_statement": ["revenue_from_operations", "ebitda", "finance_cost", "pat"],
    },
    "trend_analysis_agent": {
        "balance_sheet": ["share_capital", "networth"],
        "income_statement": ["revenue_from_operations", "pat"],
    },
    "bank_statement_analyzer": {"auxiliary_data": ["bank_statements"]},
    "cash_flow_agent": {
        "cash_flow": ["operating_activities", "investing_activities", "financing_activities", "net_change_in_cash"],
    },
    "gst_analytics_agent": {"auxiliary_data": ["gst_returns"]},
    "tax_compliance_agent": {"auxiliary_data": ["itr_filings"]},
    "related_party_detection_agent": {
        "overview": ["directors", "charges"],
        "auxiliary_data": ["roc_filings"],
    },
    "industry_intelligence_agent": {"overview": ["industry"]},
    "market_risk_agent": {"overview": ["industry"]},
}


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 1 BRIDGE  (formerly agent1_bridge.py)
# Normalises raw Agent 1 output into a clean contract for Agent 2.
# ══════════════════════════════════════════════════════════════════════════════

def _detect_source_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".pdf": "pdf_text",
        ".doc": "docx_text",
        ".docx": "docx_text",
        ".xls": "xlsx_text",
        ".xlsx": "xlsx_text",
        ".csv": "csv_text",
        ".png": "image_ocr",
        ".jpg": "image_ocr",
        ".jpeg": "image_ocr",
        ".webp": "image_ocr",
        ".tif": "image_ocr",
        ".tiff": "image_ocr",
        ".bmp": "image_ocr",
    }.get(suffix, "unknown")


def _detect_document_role(filename: str) -> str:
    name = filename.lower()
    if any(x in name for x in ["gstr", "gst"]):
        return "gst_return"
    if any(x in name for x in ["itr", "income tax"]):
        return "itr_filing"
    if any(x in name for x in ["bank", "stmt", "statement", "od", "cc", "sanction"]):
        return "bank_document"
    if any(x in name for x in ["coi", "incorporation", "moa", "aoa", "mca", "charge"]):
        return "roc_filing"
    if any(x in name for x in ["cibil", "bureau"]):
        return "bureau_report"
    if any(x in name for x in ["cash flow", "cashflow"]):
        return "cash_flow_statement"
    if any(x in name for x in ["balance", "bs", "profit", "loss", "pl", "financial"]):
        return "financial_statement"
    return "other"


def _infer_period_hint(filename: str) -> Optional[str]:
    patterns = [
        r"fy\s*([0-9]{2,4})[-_ ]?([0-9]{2,4})",
        r"([0-9]{4})[-_ ]([0-9]{2,4})",
        r"([a-z]{3,9})[_ -]?([0-9]{2,4})",
    ]
    lowered = filename.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return match.group(0)
    return None


def _collect_source_inventory(input_docs_dir: Path) -> List[Dict[str, Any]]:
    if not input_docs_dir.exists():
        return []
    items: List[Dict[str, Any]] = []
    for path in sorted(p for p in input_docs_dir.rglob('*') if p.is_file()):
        items.append(
            {
                "filename": path.name,
                "source": _detect_source_type(path),
                "document_role": _detect_document_role(path.name),
                "period_hint": _infer_period_hint(path.name),
            }
        )
    return items


def _resolve_tabs(raw: Dict[str, Any]) -> Dict[str, Any]:
    tab_data = raw.get("tab_data")
    if isinstance(tab_data, dict) and tab_data:
        return {
            "overview": dict(tab_data.get("overview", {}) or {}),
            "balance_sheet": list(tab_data.get("balance_sheet", []) or []),
            "income_statement": list(tab_data.get("income_statement", []) or []),
            "cash_flow": list(tab_data.get("cash_flow", []) or []),
        }
    return {
        "overview": dict(raw.get("overview", {}) or {}),
        "balance_sheet": list(raw.get("balance_sheet", []) or []),
        "income_statement": list(raw.get("income_statement", []) or []),
        "cash_flow": list(raw.get("cash_flow", []) or []),
    }


def _derive_overview_metrics(tab_data: Dict[str, Any]) -> Dict[str, Any]:
    overview = dict(tab_data.get("overview", {}) or {})
    bs = tab_data.get("balance_sheet", []) or []
    is_rows = tab_data.get("income_statement", []) or []
    latest_bs = bs[-1] if bs else {}
    latest_is = is_rows[-1] if is_rows else {}

    if overview.get("net_sales") is None:
        overview["net_sales"] = latest_is.get("revenue_from_operations") or latest_is.get("revenue")
    if overview.get("ebitda") is None:
        overview["ebitda"] = latest_is.get("ebitda")
    if overview.get("pat") is None:
        overview["pat"] = latest_is.get("pat")
    if overview.get("tax_expense") is None:
        overview["tax_expense"] = latest_is.get("tax_expense") or latest_is.get("tax")
    if overview.get("pbt") is None:
        overview["pbt"] = latest_is.get("pbt")
    if overview.get("networth") is None:
        overview["networth"] = latest_bs.get("networth") or latest_bs.get("equity")
    if overview.get("total_debt") is None:
        overview["total_debt"] = latest_bs.get("total_debt")
    if overview.get("metrics_year_income") is None:
        overview["metrics_year_income"] = latest_is.get("year")
    if overview.get("metrics_year_balance") is None:
        overview["metrics_year_balance"] = latest_bs.get("year")
    return overview


def _build_auxiliary_data(source_inventory: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {
        "bank_statements": [],
        "gst_returns": [],
        "itr_filings": [],
        "roc_filings": [],
    }
    for item in source_inventory:
        role = item.get("document_role")
        payload = {
            "filename": item.get("filename"),
            "source": item.get("source"),
            "period_hint": item.get("period_hint"),
        }
        if role == "bank_document":
            groups["bank_statements"].append(payload)
        elif role == "gst_return":
            groups["gst_returns"].append(payload)
        elif role == "itr_filing":
            groups["itr_filings"].append(payload)
        elif role == "roc_filing":
            groups["roc_filings"].append(payload)
    return groups


def _build_missing_fields(tab_data: Dict[str, Any]) -> Dict[str, List[str]]:
    missing: Dict[str, List[str]] = {}
    overview = tab_data.get("overview", {}) or {}
    ov_missing = [
        k for k, v in overview.items()
        if k not in _META_FIELDS and k in _OVERVIEW_PRIORITY and (v is None or v == [] or v == "")
    ]
    if ov_missing:
        missing["overview"] = ov_missing

    canonical_fields = {
        "balance_sheet": [
            "share_capital", "reserves_surplus", "networth", "total_debt",
            "current_assets", "current_liabilities", "inventory", "receivables",
            "cash_bank", "trade_payables", "fixed_assets", "total_assets", "total_liabilities",
        ],
        "income_statement": [
            "revenue_from_operations", "other_income", "cost_of_material",
            "employee_benefit_expense", "finance_cost", "depreciation",
            "total_expenses", "ebitda", "pbt", "tax_expense", "pat",
        ],
        "cash_flow": [
            "operating_activities", "investing_activities", "financing_activities", "net_change_in_cash",
        ],
    }

    for tab, fields in canonical_fields.items():
        entries = tab_data.get(tab, []) or []
        if not entries:
            missing[tab] = ["entries"]
            continue
        always_null = [field for field in fields if all((entry or {}).get(field) is None for entry in entries)]
        if always_null:
            missing[tab] = always_null
    return missing


def _entries_have_any(entries: List[Dict[str, Any]], fields: List[str]) -> bool:
    if not entries:
        return False
    for entry in entries:
        if any((entry or {}).get(field) not in (None, "", []) for field in fields):
            return True
    return False


def _build_agent3_completeness(tab_data: Dict[str, Any], auxiliary_data: Dict[str, Any]) -> Dict[str, Any]:
    overview = tab_data.get("overview", {}) or {}
    agent_flags: Dict[str, bool] = {}
    for agent_name, requirement_map in AGENT3_REQUIRED_FIELDS.items():
        ready = True
        for group, fields in requirement_map.items():
            if group == "overview":
                if not all(overview.get(field) not in (None, "", []) for field in fields):
                    ready = False
                    break
            elif group == "auxiliary_data":
                if not all(auxiliary_data.get(field) for field in fields):
                    ready = False
                    break
            else:
                if not _entries_have_any(tab_data.get(group, []) or [], fields):
                    ready = False
                    break
        agent_flags[agent_name] = ready
    return {
        "ready_for_analysis_from_agent1": all(agent_flags.values()),
        "requires_agent2_enrichment": not all(agent_flags.values()),
        "agent3_readiness": agent_flags,
    }


def normalize_agent1_output(
    raw_output: Dict[str, Any],
    input_docs_dir: Path,
    debug_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tabs = _resolve_tabs(raw_output)
    tabs["overview"] = _derive_overview_metrics(tabs)
    source_inventory = _collect_source_inventory(input_docs_dir)
    auxiliary_data = _build_auxiliary_data(source_inventory)
    missing_fields = _build_missing_fields(tabs)
    completeness = _build_agent3_completeness(tabs, auxiliary_data)
    completeness["missing_from_agent1"] = missing_fields

    summary = dict(raw_output.get("summary", {}) or {})
    if source_inventory and not summary.get("documents_processed"):
        summary["documents_processed"] = [
            {
                "filename": item["filename"],
                "doc_type": item["document_role"],
                "chars_extracted": 0,
                "tables_found": 0,
            }
            for item in source_inventory
        ]
        summary["total_documents"] = len(source_inventory)
    summary.setdefault("tabs_populated", {
        "overview": len(tabs.get("overview", {}) or {}),
        "balance_sheet": len(tabs.get("balance_sheet", []) or []),
        "income_statement": len(tabs.get("income_statement", []) or []),
        "cash_flow": len(tabs.get("cash_flow", []) or []),
    })
    if "run_timestamp" not in summary and debug_payload:
        summary["run_timestamp"] = debug_payload.get("run_timestamp")

    normalized = {
        "status": raw_output.get("status", "failed"),
        "summary": summary,
        "extraction_contract_version": "2.2",
        "tab_data": tabs,
        "auxiliary_data": auxiliary_data,
        "missing_fields": missing_fields,
        "input_completeness": completeness,
        "errors": raw_output.get("errors", []) or [],
        "source_inventory": source_inventory,
    }
    if debug_payload and debug_payload.get("structured_datasets") is not None:
        normalized["structured_datasets"] = debug_payload.get("structured_datasets")
    elif raw_output.get("structured_datasets") is not None:
        normalized["structured_datasets"] = raw_output.get("structured_datasets")
    return normalized


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 2 MAPPER  (formerly agent2_mapper.py)
# Maps Agent 1 transformation_output.json → Agent 2 payload.
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_tab_data(transformation: Dict[str, Any]) -> Dict[str, Any]:
    tab_data = transformation.get("tab_data")
    if isinstance(tab_data, dict) and tab_data:
        return {
            "overview": tab_data.get("overview", {}) or {},
            "balance_sheet": tab_data.get("balance_sheet", []) or [],
            "income_statement": tab_data.get("income_statement", []) or [],
            "cash_flow": tab_data.get("cash_flow", []) or [],
        }
    return {
        "overview": transformation.get("overview", {}) or {},
        "balance_sheet": transformation.get("balance_sheet", []) or [],
        "income_statement": transformation.get("income_statement", []) or [],
        "cash_flow": transformation.get("cash_flow", []) or [],
    }


def _map_overview(overview: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "company_name": overview.get("company_name"),
        "industry": overview.get("industry"),
        "cin": overview.get("cin"),
        "pan": overview.get("pan"),
        "gstin": overview.get("gstin"),
        "address": overview.get("address") or overview.get("registered_address"),
        "directors": overview.get("directors") or [],
        "incorporation_date": overview.get("incorporation_date") or overview.get("date_of_incorporation"),
        "charges": overview.get("charges") or [],
        "legal_cases": overview.get("legal_cases") or [],
    }


def _map_balance_sheet(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mapped: List[Dict[str, Any]] = []
    for row in rows or []:
        mapped.append(
            {
                "year": row.get("year"),
                "total_assets": row.get("total_assets"),
                "total_liabilities": row.get("total_liabilities"),
                "equity": (
                    row.get("equity") if row.get("equity") is not None
                    else row.get("networth") if row.get("networth") is not None
                    else row.get("reserves_surplus")
                ),
                "current_assets": row.get("current_assets"),
                "current_liabilities": row.get("current_liabilities"),
                "fixed_assets": row.get("fixed_assets"),
                "long_term_debt": (
                    row.get("long_term_debt") if row.get("long_term_debt") is not None
                    else row.get("long_term_borrowing")
                ),
                "cash_and_equivalents": (
                    row.get("cash_and_equivalents") if row.get("cash_and_equivalents") is not None
                    else row.get("cash_and_bank") if row.get("cash_and_bank") is not None
                    else row.get("cash_bank")
                ),
                "source_document": row.get("source_document"),
            }
        )
    return mapped


def _map_income_statement(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mapped: List[Dict[str, Any]] = []
    for row in rows or []:
        mapped.append(
            {
                "year": row.get("year"),
                "revenue": (
                    row.get("revenue") if row.get("revenue") is not None
                    else row.get("revenue_from_operations") if row.get("revenue_from_operations") is not None
                    else row.get("net_sales")
                ),
                "gross_profit": row.get("gross_profit"),
                "ebitda": row.get("ebitda"),
                "ebit": row.get("ebit"),
                "pat": row.get("pat"),
                "operating_expenses": (
                    row.get("operating_expenses") if row.get("operating_expenses") is not None
                    else row.get("total_expenses")
                ),
                "depreciation": row.get("depreciation"),
                "interest_expense": (
                    row.get("interest_expense") if row.get("interest_expense") is not None
                    else row.get("finance_cost")
                ),
                "tax": row.get("tax") if row.get("tax") is not None else row.get("tax_expense"),
                "source_document": row.get("source_document"),
            }
        )
    return mapped


def _map_cash_flow(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mapped: List[Dict[str, Any]] = []
    for row in rows or []:
        mapped.append(
            {
                "year": row.get("year"),
                "operating_cash_flow": (
                    row.get("operating_cash_flow") if row.get("operating_cash_flow") is not None
                    else row.get("operating_activities") if row.get("operating_activities") is not None
                    else row.get("cash_from_operating_activities")
                ),
                "investing_cash_flow": (
                    row.get("investing_cash_flow") if row.get("investing_cash_flow") is not None
                    else row.get("investing_activities") if row.get("investing_activities") is not None
                    else row.get("cash_from_investing_activities")
                ),
                "financing_cash_flow": (
                    row.get("financing_cash_flow") if row.get("financing_cash_flow") is not None
                    else row.get("financing_activities") if row.get("financing_activities") is not None
                    else row.get("cash_from_financing_activities")
                ),
                "net_cash_flow": (
                    row.get("net_cash_flow") if row.get("net_cash_flow") is not None
                    else row.get("net_change_in_cash")
                ),
                "capital_expenditure": row.get("capital_expenditure"),
                "source_document": row.get("source_document"),
            }
        )
    return mapped


def map_transformation_output_to_agent2_payload(transformation_output_path: Path) -> Dict[str, Any]:
    """
    Bridge Agent 1 output into Agent 2's expected payload.
    Supports both legacy Agent 1 output and the cleaned minimal Agent 1 contract.
    """
    transformation = json.loads(transformation_output_path.read_text(encoding="utf-8"))
    tabs = _resolve_tab_data(transformation)

    archive_name = transformation.get("archive_file")
    debug_payload: Dict[str, Any] = {}
    if archive_name:
        archive_stem = Path(archive_name).stem
        debug_path = transformation_output_path.parent / f"{archive_stem}_debug.json"
        if debug_path.exists():
            debug_payload = json.loads(debug_path.read_text(encoding="utf-8"))

    summary = transformation.get("summary", {}) or {}
    documents_processed = summary.get("documents_processed", []) or []
    tabs_populated = summary.get("tabs_populated", {}) or {}
    structured = transformation.get("structured_datasets") or debug_payload.get("structured_datasets") or {}

    mapped_tabs = {
        "overview": _map_overview(tabs.get("overview", {}) or {}),
        "balance_sheet": _map_balance_sheet(tabs.get("balance_sheet", []) or []),
        "income_statement": _map_income_statement(tabs.get("income_statement", []) or []),
        "cash_flow": _map_cash_flow(tabs.get("cash_flow", []) or []),
    }

    auxiliary_data: Dict[str, Any] = dict(transformation.get("auxiliary_data", {}) or {})
    bank_txns = (transformation.get("bank_statements") or {}).get("transactions")
    if bank_txns:
        auxiliary_data["bank_statements"] = bank_txns
    gst_data = transformation.get("gst_data")
    if gst_data:
        auxiliary_data["gst_data"] = gst_data
    itr_data = transformation.get("itr_data")
    if itr_data:
        auxiliary_data["itr_data"] = itr_data

    return {
        "status": transformation.get("status", "failed"),
        "summary": {
            "run_timestamp": summary.get("run_timestamp") or transformation.get("run_id"),
            "total_documents": summary.get("total_documents", len(documents_processed)) or summary.get("files_used", 0),
            "documents_processed": documents_processed,
            "tabs_populated": {
                "overview": tabs_populated.get("overview", len(mapped_tabs.get("overview", {}) or {})),
                "balance_sheet": tabs_populated.get("balance_sheet", len(mapped_tabs.get("balance_sheet", []) or [])),
                "income_statement": tabs_populated.get("income_statement", len(mapped_tabs.get("income_statement", []) or [])),
                "cash_flow": tabs_populated.get("cash_flow", len(mapped_tabs.get("cash_flow", []) or [])),
            },
            "error_count": summary.get("error_count", len(transformation.get("errors", []) or [])),
        },
        "tab_data": mapped_tabs,
        "auxiliary_data": auxiliary_data,
        "structured_datasets": structured,
        "missing_fields": transformation.get("missing_fields", {}) or {},
        "input_completeness": transformation.get("input_completeness", {}) or {},
        "errors": transformation.get("errors", []) or [],
    }


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 3 MAPPER  (formerly agent3_mapper.py)
# Maps Agent 2 enrich_output → Agent 3 Agent2Output schema.
# ══════════════════════════════════════════════════════════════════════════════

def _clean_none_list(values: Any) -> List[Any]:
    if not values:
        return []
    if isinstance(values, list):
        return [v for v in values if v is not None]
    return []


def _map_itr_filings(raw_scraped: Dict[str, Any]) -> List[Any]:
    """
    Build the itr_filings list for Agent2Output.

    Priority: rich itr_data dict (from transformation agent) wrapped as a
    single-element list with a sentinel key, so tax_compliance_agent.py can
    detect and use the full structured format. Falls back to legacy list.
    """
    itr_data = raw_scraped.get("itr_data")
    if itr_data and isinstance(itr_data, dict):
        return [{"__rich_itr_data__": True, **itr_data}]
    return raw_scraped.get("itr_filings", [])


def _map_gst_returns(raw_scraped: Dict[str, Any]) -> List[Any]:
    """
    Build the gst_returns list for Agent2Output.

    Priority order:
    1. rich gst_data dict (from transformation agent via auxiliary_data) —
       wrapped in a single-element list so gst_agent.py can detect and use it.
    2. Legacy gst_returns list (from web scraper raw_results or old format).
    """
    gst_data = raw_scraped.get("gst_data")
    if gst_data and isinstance(gst_data, dict):
        return [{"__rich_gst_data__": True, **gst_data}]
    return raw_scraped.get("gst_returns", [])


def _normalize_gst_data(gst_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalise a new-format gst_data dict (with gstr1_annual / gstr3b_annual keys)
    into the legacy flat structure that gst_agent.py expects:
        { period, gst_sales: { annual_taxable_value, monthly_taxable_value },
          gst_tax, gst_consistency, sales_breakdown, trend_analysis, risk_flags }
    Also handles the old flat format transparently (passes through unchanged).
    All monetary values must be in crores.
    """
    # Already in legacy format — pass through
    if "gst_sales" in gst_data:
        return gst_data

    gstr1 = gst_data.get("gstr1_annual") or {}
    gstr3b = gst_data.get("gstr3b_annual") or {}
    annual = gstr1.get("annual_total") or {}
    monthly = gstr1.get("monthly") or []
    consistency = gst_data.get("gst_consistency") or {}
    sales_breakdown = gst_data.get("sales_breakdown") or {}
    trend = gst_data.get("trend_analysis") or {}
    risk_flags = gst_data.get("risk_flags") or {}

    return {
        "period": gst_data.get("period"),
        "gst_sales": {
            "annual_taxable_value": annual.get("total_taxable_cr"),
            "monthly_taxable_value": [
                {"month": m.get("month"), "value": m.get("total_taxable_cr")}
                for m in monthly
            ],
        },
        "gst_tax": {
            "igst": gstr3b.get("igst_cr") or annual.get("igst_paid_cr"),
            "cgst": gstr3b.get("cgst_cr"),
            "sgst": gstr3b.get("sgst_cr"),
            "total_tax_paid": (
                (gstr3b.get("igst_cr") or 0)
                + (gstr3b.get("cgst_cr") or 0)
                + (gstr3b.get("sgst_cr") or 0)
            ) or None,
        },
        "gst_consistency": {
            "gstr1_total_sales": consistency.get("gstr1_total_sales_cr"),
            "gstr3b_total_sales": consistency.get("gstr3b_total_sales_cr"),
            "difference": consistency.get("difference"),
            "match": consistency.get("match"),
        },
        "sales_breakdown": {
            "b2b_sales": sales_breakdown.get("b2b_domestic_cr"),
            "export_sales": sales_breakdown.get("export_zero_rated_cr"),
            "domestic_sales": sales_breakdown.get("b2b_domestic_cr"),
        },
        "trend_analysis": {
            "average_monthly_sales": trend.get("avg_monthly_sales_cr"),
            "highest_month": trend.get("highest_month"),
            "lowest_month": trend.get("lowest_month"),
            "sales_volatility": trend.get("sales_volatility"),
            "growth_pattern": trend.get("growth_pattern"),
        },
        "risk_flags": risk_flags,
    }


def _normalize_bank_transactions(bank_statements: Any) -> List[Dict[str, Any]]:
    """
    Normalise bank statement transactions to the flat format that
    bank_statement_agent.py expects: { date, amount, type, is_bounce, is_cash }.

    Handles two source formats:
      - New format: { credit_cr, debit_cr, balance_cr, ... } — no amount/type keys
      - Old format: { amount, type, ... } — already correct, pass through
    """
    txns = []
    if isinstance(bank_statements, dict):
        raw = bank_statements.get("transactions") or []
    elif isinstance(bank_statements, list):
        raw = bank_statements
    else:
        return txns

    for txn in raw:
        if not isinstance(txn, dict):
            continue
        # Old format already has amount + type
        if "amount" in txn and "type" in txn:
            txns.append(txn)
            continue
        # New format: derive amount and type from credit_cr / debit_cr
        credit = txn.get("credit_cr")
        debit = txn.get("debit_cr")
        if credit is not None and credit > 0:
            txns.append({**txn, "amount": credit, "type": "credit"})
        elif debit is not None and debit > 0:
            txns.append({**txn, "amount": debit, "type": "debit"})
        # Skip zero-amount opening/closing balance rows
    return txns


def map_db_json_to_agent3_payload(db_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Bridge the enriched-extraction JSON retrieved from the database directly
    into Agent 3's expected Agent2Output schema, bypassing Agents 1 and 2.

    Supports both old and new DB JSON formats transparently.
    ratio_analysis_data rows are merged into balance_sheet / income_statement
    by year to supply fields that Agent 3 needs (current_assets, ebitda, pat …).
    Year label alignment: builds a secondary alias map for common mismatches
    (e.g. balance_sheet "2026-Dec" → ratio_analysis_data "2027-9M").
    """
    overview = db_json.get("overview", {}) or {}
    balance_sheet_rows = db_json.get("balance_sheet", []) or []
    income_statement_rows = db_json.get("income_statement", []) or []
    cash_flow_rows = db_json.get("cash_flow", []) or []
    ratio_by_year: Dict[str, Dict[str, Any]] = {
        r["year"]: r
        for r in (db_json.get("ratio_analysis_data", []) or [])
        if r.get("year")
    }
    bank_statements_raw = db_json.get("bank_statements")
    bank_txns = _normalize_bank_transactions(bank_statements_raw)
    gst_data = db_json.get("gst_data")
    itr_data = db_json.get("itr_data")
    summary = db_json.get("summary", {}) or {}

    # ── total_debt: prefer provisional if audited year shows zero ────────────
    total_debt = overview.get("total_debt")
    if (total_debt is None or total_debt == 0.0) and overview.get("total_debt_provisional_9m") is not None:
        total_debt = overview.get("total_debt_provisional_9m")

    # ── enriched_overview ────────────────────────────────────────────────────
    enriched_overview = {
        "cin": overview.get("cin"),
        "pan": overview.get("pan"),
        "company_name": overview.get("company_name"),
        "net_sales": overview.get("net_sales"),
        "ebitda": overview.get("ebitda"),
        "pat": overview.get("pat"),
        "networth": overview.get("networth"),
        "total_debt": total_debt,
        "metrics_year_income": overview.get("metrics_year_income"),
        "metrics_year_balance": overview.get("metrics_year_balance"),
        "gstin": overview.get("gstin"),
        "incorporation_date": overview.get("incorporation_date") or overview.get("date_of_incorporation"),
        "registered_address": overview.get("registered_address") or overview.get("address"),
        "industry": overview.get("industry"),
        "directors": _clean_none_list(overview.get("directors")),
        "charges": _clean_none_list(overview.get("charges")),
        "legal_cases": _clean_none_list(overview.get("legal_cases")),
    }

    # ── year alias map: balance_sheet / income_statement year → ratio year ───
    # Handles cases where year labels differ between tables in the same JSON.
    # Build by matching revenue figures when year strings don't match directly.
    ratio_revenue_map: Dict[float, str] = {
        r.get("total_revenue_from_operations"): yr
        for yr, r in ratio_by_year.items()
        if r.get("total_revenue_from_operations") is not None
    }

    def _find_ratio(year: Optional[str], revenue: Optional[float]) -> Dict[str, Any]:
        """Return ratio row by year string, falling back to revenue match."""
        if year and year in ratio_by_year:
            return ratio_by_year[year]
        if revenue is not None and revenue in ratio_revenue_map:
            return ratio_by_year.get(ratio_revenue_map[revenue], {})
        return {}

    # ── balance_sheet — merge ratio row for enriched fields ──────────────────
    mapped_balance_sheet = []
    for row in balance_sheet_rows:
        year = row.get("year")
        # Balance sheet doesn't have revenue — match by current_assets as tiebreak
        ratio = ratio_by_year.get(year) or {}
        if not ratio:
            # Try matching by total_current_assets value
            ca = row.get("total_current_assets") or row.get("current_assets")
            for r in ratio_by_year.values():
                if ca is not None and r.get("current_assets") == ca:
                    ratio = r
                    break
        mapped_balance_sheet.append({
            "year": year,
            "share_capital": row.get("share_capital"),
            "reserves_surplus": row.get("reserves_surplus"),
            "long_term_borrowing": row.get("long_term_borrowing"),
            "short_term_borrowing": row.get("short_term_borrowing"),
            "trade_payables": row.get("trade_payables"),
            "fixed_assets": row.get("fixed_assets") or row.get("fixed_assets_net"),
            "current_assets": ratio.get("current_assets") or row.get("total_current_assets"),
            "current_liabilities": ratio.get("current_liabilities") or row.get("total_current_liabilities"),
            "networth": ratio.get("shareholder_equity") or row.get("total_shareholders_funds"),
            "source_document": row.get("source_document"),
            "source_documents": row.get("source_documents") or [],
        })

    # ── income_statement — merge ebitda / pat from ratio row ─────────────────
    mapped_income_statement = []
    for row in income_statement_rows:
        year = row.get("year")
        revenue = row.get("revenue_from_operations")
        ratio = _find_ratio(year, revenue)
        mapped_income_statement.append({
            "year": year,
            "revenue_from_operations": revenue,
            "other_income": row.get("other_income"),
            "cost_of_material": row.get("cost_of_material"),
            "employee_benefit_expense": row.get("employee_benefit_expense"),
            "finance_cost": row.get("finance_cost"),
            "depreciation": row.get("depreciation"),
            "ebitda": ratio.get("ebitda") or row.get("ebitda"),
            "pat": ratio.get("net_income") or row.get("net_profit_pat"),
            "source_document": row.get("source_document"),
            "source_documents": row.get("source_documents") or [],
        })

    # ── cash_flow — remap field names ────────────────────────────────────────
    mapped_cash_flow = []
    for row in cash_flow_rows:
        op = row.get("operating_activities")
        inv = row.get("investing_activities")
        fin = row.get("financing_activities")
        mapped_cash_flow.append({
            "year": row.get("year"),
            "cash_from_operating_activities": op.get("net_cash_from_operating") if isinstance(op, dict) else op,
            "cash_from_investing_activities": inv.get("net_cash_from_investing") if isinstance(inv, dict) else inv,
            "cash_from_financing_activities": fin.get("net_cash_from_financing") if isinstance(fin, dict) else fin,
            "net_change_in_cash": row.get("net_change_in_cash"),
        })

    # ── gst_returns — normalise new format to legacy flat structure ──────────
    if gst_data:
        gst_data = _normalize_gst_data(gst_data)

    return {
        "status": db_json.get("status", "success"),
        "summary": {
            "run_timestamp": summary.get("run_timestamp"),
            "fields_retrieved": 0,
            "fields_flagged": 0,
            "sources_used": [],
            "errors": db_json.get("errors", []),
        },
        "enriched_overview": enriched_overview,
        "retrieved_fields": [],
        "flagged_fields": [],
        "balance_sheet": mapped_balance_sheet,
        "income_statement": mapped_income_statement,
        "cash_flow": mapped_cash_flow,
        "bank_statements": bank_txns,
        "gst_returns": [{"__rich_gst_data__": True, **gst_data}] if gst_data else [],
        "itr_filings": [{"__rich_itr_data__": True, **itr_data}] if itr_data else [],
        "roc_filings": [],
    }


def map_enrich_output_to_agent3_payload(enrich_output: Dict[str, Any]) -> Dict[str, Any]:
    """
    Bridge Agent 2's current EnrichOutput contract into Agent 3's expected
    Agent2Output contract without changing either standalone codebase.
    """
    tabs = enrich_output.get("enriched_tabs", {}) or {}
    overview = tabs.get("overview", {}) or {}
    balance_sheet = tabs.get("balance_sheet", []) or []
    income_statement = tabs.get("income_statement", []) or []
    cash_flow = tabs.get("cash_flow", []) or []

    latest_bs = balance_sheet[-1] if balance_sheet else {}
    latest_is = income_statement[-1] if income_statement else {}

    enriched_overview = {
        "cin": overview.get("cin"),
        "pan": overview.get("pan"),
        "company_name": overview.get("company_name"),
        "net_sales": latest_is.get("revenue") or latest_is.get("revenue_from_operations"),
        "ebitda": latest_is.get("ebitda"),
        "pat": latest_is.get("pat"),
        "networth": latest_bs.get("equity") or latest_bs.get("networth") or latest_bs.get("reserves_surplus"),
        "total_debt": (
            latest_bs.get("total_debt") if latest_bs.get("total_debt") is not None
            else (latest_bs.get("long_term_debt") or 0) + (latest_bs.get("short_term_borrowing") or 0)
        ),
        "metrics_year_income": latest_is.get("year"),
        "metrics_year_balance": latest_bs.get("year"),
        "gstin": overview.get("gstin"),
        "incorporation_date": overview.get("incorporation_date") or overview.get("date_of_incorporation"),
        "registered_address": overview.get("address") or overview.get("registered_address"),
        "industry": overview.get("industry"),
        "directors": _clean_none_list(overview.get("directors")),
        "charges": _clean_none_list(overview.get("charges")),
        "legal_cases": _clean_none_list(overview.get("legal_cases")),
    }

    mapped_balance_sheet = []
    for row in balance_sheet:
        mapped_balance_sheet.append(
            {
                "year": row.get("year"),
                "share_capital": row.get("share_capital"),
                "reserves_surplus": row.get("reserves_surplus"),
                "long_term_borrowing": row.get("long_term_debt") or row.get("long_term_borrowing"),
                "short_term_borrowing": row.get("short_term_borrowing"),
                "trade_payables": row.get("trade_payables"),
                "fixed_assets": row.get("fixed_assets"),
                "source_document": row.get("source_document"),
                "source_documents": [row.get("source_document")] if row.get("source_document") else [],
            }
        )

    mapped_income_statement = []
    for row in income_statement:
        mapped_income_statement.append(
            {
                "year": row.get("year"),
                "revenue_from_operations": row.get("revenue") or row.get("revenue_from_operations"),
                "other_income": row.get("other_income"),
                "cost_of_material": row.get("cost_of_material"),
                "employee_benefit_expense": row.get("employee_benefit_expense"),
                "finance_cost": row.get("interest_expense") or row.get("finance_cost"),
                "depreciation": row.get("depreciation"),
                "source_document": row.get("source_document"),
                "source_documents": [row.get("source_document")] if row.get("source_document") else [],
            }
        )

    mapped_cash_flow = []
    for row in cash_flow:
        mapped_cash_flow.append(
            {
                "year": row.get("year"),
                "cash_from_operating_activities": row.get("operating_cash_flow") or row.get("cash_from_operating_activities"),
                "cash_from_investing_activities": row.get("investing_cash_flow") or row.get("cash_from_investing_activities"),
                "cash_from_financing_activities": row.get("financing_cash_flow") or row.get("cash_from_financing_activities"),
                "net_change_in_cash": row.get("net_cash_flow") or row.get("net_change_in_cash"),
            }
        )

    summary = enrich_output.get("summary", {}) or {}
    mapped_summary = {
        "run_timestamp": summary.get("run_timestamp"),
        "fields_retrieved": summary.get("fields_scraped"),
        "fields_flagged": summary.get("fields_flagged"),
        "sources_used": summary.get("sources_used", []),
        "errors": summary.get("errors", []),
    }

    mapped_retrieved = []
    for item in enrich_output.get("retrieved_fields", []) or []:
        mapped_retrieved.append(
            {
                "field_name": getattr(item, "field_name", None) if not isinstance(item, dict) else item.get("field_name"),
                "value": getattr(item, "value", None) if not isinstance(item, dict) else item.get("value"),
                "source": getattr(item, "source", None) if not isinstance(item, dict) else item.get("source"),
                "confidence": getattr(item, "confidence", None) if not isinstance(item, dict) else item.get("confidence"),
                "notes": getattr(item, "flag_reason", None) if not isinstance(item, dict) else item.get("flag_reason"),
            }
        )

    mapped_flagged = []
    for item in enrich_output.get("flagged_manual", []) or []:
        if isinstance(item, dict):
            mapped_flagged.append(item)
        else:
            mapped_flagged.append(item.model_dump())

    raw_scraped = enrich_output.get("raw_scraped_data", {}) or {}

    return {
        "status": enrich_output.get("status"),
        "summary": mapped_summary,
        "enriched_overview": enriched_overview,
        "retrieved_fields": mapped_retrieved,
        "flagged_fields": mapped_flagged,
        "balance_sheet": mapped_balance_sheet,
        "income_statement": mapped_income_statement,
        "cash_flow": mapped_cash_flow,
        "bank_statements": raw_scraped.get("bank_statements", []),
        "gst_returns": _map_gst_returns(raw_scraped),
        "itr_filings": _map_itr_filings(raw_scraped),
        "roc_filings": raw_scraped.get("roc_filings", []),
    }
