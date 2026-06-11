from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()  # must be before any docpipe imports

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from agent1_patch_source.schema import (
    EnrichedOverview,
    BalanceSheetData,
    IncomeStatementData,
    CashFlowData,
)
from agent1_patch_source.pipeline import run_multi_document_pipeline
from agent1_patch_source.llmextract import LLMError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DOCUMENTS_DIR = Path("documents")
OUTPUT_DIR = Path("outputs")
INCLUDE_RAW = False

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp",
    ".docx", ".doc",
    ".xlsx", ".xls",
    ".csv",
}

SCHEMA_FILE_PATTERNS: dict[str, list[str]] = {
    "overview": [
        "coi", "certificate of incorporation",
        "moa", "aoa", "memorandum",
        "gst registration",
        "company_profile", "company profile",
        "cibil",
        "itr", "income tax return",
        "pan",
        "director", "promoter",
    ],
    "balance_sheet": [
        "balance", "bs_", "_bs_", "provisional_bs",
        "book", "audited",
        "annual",
    ],
    "income_statement": [
        "profit", "loss", "p&l", "pl_", "_pl_",
        "income_statement", "audited_pl",
        "provisional_pl",
        "detailed_pl",
        "annual",
    ],
    "cash_flow": [
        "cash flow", "cashflow", "cash_flow",
    ],
}

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
    "bank_statement_analyzer": {
        "auxiliary_data": ["bank_statements"],
    },
    "cash_flow_agent": {
        "cash_flow": [
            "operating_activities",
            "investing_activities",
            "financing_activities",
            "net_change_in_cash",
        ],
    },
    "gst_analytics_agent": {
        "auxiliary_data": ["gst_returns"],
    },
    "tax_compliance_agent": {
        "auxiliary_data": ["itr_filings"],
    },
    "related_party_detection_agent": {
        "overview": ["directors", "charges"],
        "auxiliary_data": ["roc_filings"],
    },
    "industry_intelligence_agent": {
        "overview": ["industry"],
    },
    "market_risk_agent": {
        "overview": ["industry"],
    },
}


def route_files(all_files: list[str], schema_name: str) -> list[str]:
    patterns = SCHEMA_FILE_PATTERNS.get(schema_name, [])
    financial_schemas = {"balance_sheet", "income_statement", "cash_flow"}

    matched: list[str] = []
    unmatched: list[str] = []

    for fp in all_files:
        name_lower = Path(fp).name.lower()
        hit = any(p in name_lower for p in _all_patterns())
        if any(p in name_lower for p in patterns):
            matched.append(fp)
        elif not hit:
            unmatched.append(fp)

    selected = matched + unmatched if schema_name in financial_schemas else matched
    if not selected:
        logger.warning("[route] No files matched for schema '%s' — using all files as fallback.", schema_name)
        return all_files
    return selected


def _all_patterns() -> list[str]:
    return [p for patterns in SCHEMA_FILE_PATTERNS.values() for p in patterns]


def build_missing_fields(tab_data: dict[str, Any]) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}

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
            "operating_activities",
            "investing_activities",
            "financing_activities",
            "net_change_in_cash",
        ],
    }

    for tab, fields in canonical_fields.items():
        entries = tab_data.get(tab, []) or []
        if not entries:
            missing[tab] = ["entries"]
            continue
        always_null = [field for field in fields if all(entry.get(field) is None for entry in entries)]
        if always_null:
            missing[tab] = always_null

    return missing


def collect_files(folder: Path) -> list[str]:
    if not folder.exists():
        raise SystemExit(
            f"[ERROR] Documents folder not found: '{folder.resolve()}'\n"
            f"        Create the folder and drop your files inside it."
        )
    return sorted([
        str(f) for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ])


def detect_source_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
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


def detect_document_role(path: str) -> str:
    name = Path(path).name.lower()
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


def infer_period_hint(filename: str) -> str | None:
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


def normalize_tab_data(tab_data: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "overview": dict(tab_data.get("overview", {}) or {}),
        "balance_sheet": list(tab_data.get("balance_sheet", []) or []),
        "income_statement": list(tab_data.get("income_statement", []) or []),
        "cash_flow": list(tab_data.get("cash_flow", []) or []),
    }
    return normalized


def derive_overview_metrics(tab_data: dict[str, Any]) -> dict[str, Any]:
    overview = dict(tab_data.get("overview", {}) or {})
    income_entries = tab_data.get("income_statement", []) or []
    balance_entries = tab_data.get("balance_sheet", []) or []

    latest_income = next((e for e in reversed(income_entries) if e.get("year")), None)
    latest_balance = next((e for e in reversed(balance_entries) if e.get("year")), None)

    if latest_income:
        overview.setdefault("net_sales", latest_income.get("revenue_from_operations"))
        overview.setdefault("ebitda", latest_income.get("ebitda"))
        overview.setdefault("pat", latest_income.get("pat"))
        overview.setdefault("pbt", latest_income.get("pbt"))
        overview.setdefault("tax_expense", latest_income.get("tax_expense"))
        overview.setdefault("metrics_year_income", latest_income.get("year"))
    if latest_balance:
        overview.setdefault("networth", latest_balance.get("networth") or latest_balance.get("reserves_surplus"))
        overview.setdefault("total_debt", latest_balance.get("total_debt") or _sum_numbers([
            latest_balance.get("long_term_borrowing"),
            latest_balance.get("short_term_borrowing"),
        ]))
        overview.setdefault("metrics_year_balance", latest_balance.get("year"))
    return overview


def _sum_numbers(values: list[Any]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return sum(nums) if nums else None


def build_source_inventory(file_paths: list[str]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for fp in file_paths:
        path = Path(fp)
        inventory.append({
            "filename": path.name,
            "source": detect_source_type(fp),
            "document_role": detect_document_role(fp),
            "pages_used": 1,
            "chars": None,
        })
    return inventory


def build_auxiliary_data(file_paths: list[str], tabs: dict[str, Any]) -> dict[str, Any]:
    overview = tabs.get("overview", {}) or {}
    bank_docs: list[dict[str, Any]] = []
    gst_docs: list[dict[str, Any]] = []
    itr_docs: list[dict[str, Any]] = []
    roc_docs: list[dict[str, Any]] = []

    for fp in file_paths:
        name = Path(fp).name
        lower = name.lower()
        base_payload = {
            "filename": name,
            "source": detect_source_type(fp),
            "period_hint": infer_period_hint(name),
        }
        if any(x in lower for x in ["bank", "stmt", "statement", "od", "cc", "sanction"]):
            bank_docs.append({
                **base_payload,
                "document_type": "bank_statement" if any(x in lower for x in ["stmt", "statement", "od", "cc"]) else "bank_facility_document",
            })
        if any(x in lower for x in ["gstr", "gst"]):
            gst_docs.append({
                **base_payload,
                "gstin": overview.get("gstin"),
            })
        if any(x in lower for x in ["itr", "income tax"]):
            itr_docs.append({
                **base_payload,
                "pan": overview.get("pan"),
            })
        if any(x in lower for x in ["coi", "incorporation", "moa", "aoa", "mca", "charge"]):
            roc_docs.append({
                **base_payload,
                "cin": overview.get("cin"),
            })

    if overview.get("charges"):
        roc_docs.append({
            "document_type": "roc_charge_summary",
            "cin": overview.get("cin"),
            "charges": overview.get("charges"),
        })

    return {
        "bank_statements": bank_docs,
        "gst_returns": gst_docs,
        "itr_filings": itr_docs,
        "roc_filings": roc_docs,
    }


def _entries_have_any(entries: list[dict[str, Any]], fields: list[str]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    if not entries:
        return False, fields
    for field in fields:
        if all(e.get(field) in (None, [], "") for e in entries):
            missing.append(field)
    return len(missing) == 0, missing


def build_agent3_completeness(tab_data: dict[str, Any], auxiliary_data: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    all_missing: list[str] = []

    for agent_name, requirement_groups in AGENT3_REQUIRED_FIELDS.items():
        agent_missing: list[str] = []
        for group_name, fields in requirement_groups.items():
            if group_name == "auxiliary_data":
                for field in fields:
                    value = auxiliary_data.get(field)
                    if not value:
                        agent_missing.append(f"auxiliary_data.{field}")
            elif group_name == "overview":
                overview = tab_data.get("overview", {}) or {}
                for field in fields:
                    if overview.get(field) in (None, [], ""):
                        agent_missing.append(f"overview.{field}")
            else:
                ok, missing_fields = _entries_have_any(tab_data.get(group_name, []) or [], fields)
                if not ok:
                    agent_missing.extend([f"{group_name}.{field}" for field in missing_fields])

        checks[agent_name] = {
            "ready_from_agent1": len(agent_missing) == 0,
            "missing_from_agent1": agent_missing,
        }
        all_missing.extend(agent_missing)

    deduped_missing = sorted(set(all_missing))
    return {
        "ready_for_scraper": True,
        "ready_for_analysis_from_agent1": all(not v["missing_from_agent1"] for v in checks.values()),
        "requires_agent2_enrichment": len(deduped_missing) > 0,
        "missing_from_agent1": deduped_missing,
        "agent_checks": checks,
        "notes": [
            "This readiness report reflects Agent 1 extraction only.",
            "Fields listed as missing_from_agent1 are expected candidates for Agent 2 enrichment or later deterministic derivation.",
        ],
    }


async def process_schema(
    name: str,
    model_cls,
    doc_hint: str,
    file_paths: list[str],
) -> tuple[str, Any, list[str]]:
    selected = route_files(file_paths, name)
    logger.info("[%s] routing %d/%d files: %s", name, len(selected), len(file_paths), [Path(f).name for f in selected])

    full_hint = (
        doc_hint
        + "\n\nCRITICAL EXTRACTION RULES:\n"
        "- Always extract ALL years present.\n"
        "- Never return a single combined year.\n"
        "- Output must contain multiple entries if multiple years exist.\n"
    )

    try:
        result = await run_multi_document_pipeline(
            file_paths=selected,
            model_cls=model_cls,
            doc_hint=full_hint,
        )
    except (ValueError, LLMError) as exc:
        logger.error("[%s] extraction failed: %s", name, exc)
        return name, None, selected

    data = json.loads(result.data.model_dump_json())
    if "entries" in data:
        years = sorted({e.get("year") for e in data["entries"] if e.get("year")})
        logger.info("[%s] years captured: %s", name, years)
        if len(years) <= 1:
            logger.warning("[%s] only one year detected — possible extraction issue", name)

    return name, data, selected


SCHEMAS = [
    {
        "name": "overview",
        "schema": EnrichedOverview,
        "doc_hint": (
            "Extract company identity fields: CIN, PAN, GSTIN, company name, "
            "registered address, incorporation date, industry, directors list, "
            "and any charge or legal case records from MCA filings. "
            "Also extract overview financial metrics if clearly present: EBITDA, PAT, PBT, tax expense, net worth and total debt."
        ),
    },
    {
        "name": "balance_sheet",
        "schema": BalanceSheetData,
        "doc_hint": (
            "Extract balance sheet data. Extract share capital, reserves & surplus, net worth, total debt, current assets, current liabilities, inventory, receivables, cash & bank, trade payables, fixed assets, total assets and total liabilities. "
            "MANDATORY: return a list where each entry represents EXACTLY ONE year. "
            "Normalize years like FY23 or 2022-23 into YYYY format (e.g., 2023)."
        ),
    },
    {
        "name": "income_statement",
        "schema": IncomeStatementData,
        "doc_hint": (
            "Extract profit & loss / income statement data. Extract revenue from operations, other income, cost of material, employee benefit expense, finance cost, depreciation, total expenses, EBITDA, PBT, tax expense and PAT. "
            "MANDATORY: return a list where each entry represents EXACTLY ONE year. "
            "Normalize years like FY23 or 2022-23 into YYYY format (e.g., 2023)."
        ),
    },
    {
        "name": "cash_flow",
        "schema": CashFlowData,
        "doc_hint": (
            "Extract cash flow statement data. Extract operating_activities, investing_activities, financing_activities and net_change_in_cash. "
            "MANDATORY: return a list where each entry represents EXACTLY ONE year. "
            "Normalize years like FY23 or 2022-23 into YYYY format (e.g., 2023)."
        ),
    },
]


async def run(documents_dir: Path) -> dict[str, Any]:
    """
    Core extraction logic. Accepts a documents directory, returns the output dict.
    Called directly by the orchestrator (in-process) or by main() for standalone use.
    """
    file_paths = collect_files(documents_dir)

    logger.info("=" * 55)
    logger.info("Transformation Agent — multi-document extraction")
    logger.info("Documents folder : %s", documents_dir.resolve())
    logger.info("Files found      : %d", len(file_paths))
    for fp in file_paths:
        logger.info("  → %s", Path(fp).name)

    if not file_paths:
        logger.error("No supported files found. Supported: %s", ", ".join(sorted(SUPPORTED_EXTENSIONS)))
        return {"status": "failed", "errors": ["No supported files found"], "tab_data": {}}

    results = await asyncio.gather(*[
        process_schema(
            name=job["name"],
            model_cls=job["schema"],
            doc_hint=job["doc_hint"],
            file_paths=file_paths,
        )
        for job in SCHEMAS
    ])

    tab_data: dict[str, Any] = {}
    schema_inputs: dict[str, list[str]] = {}
    for name, data, selected_files in results:
        schema_inputs[name] = [Path(f).name for f in selected_files]
        if data is None:
            tab_data[name] = [] if name != "overview" else {}
            continue
        tab_data[name] = data["entries"] if "entries" in data else data

    tab_data = normalize_tab_data(tab_data)
    tab_data["overview"] = derive_overview_metrics(tab_data)

    overview = tab_data.get("overview", {})
    company_identifiers = {
        "company_name": overview.get("company_name"),
        "cin": overview.get("cin"),
        "pan": overview.get("pan"),
        "gstin": overview.get("gstin"),
        "industry": overview.get("industry"),
        "directors": overview.get("directors", []),
    }

    missing_fields = build_missing_fields(tab_data)
    auxiliary_data = build_auxiliary_data(file_paths, tab_data)
    input_completeness = build_agent3_completeness(tab_data, auxiliary_data)

    populated = [k for k, v in tab_data.items() if v]
    status = "success" if len(populated) == len(SCHEMAS) else "partial_success" if populated else "failed"
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    source_inventory = build_source_inventory(file_paths)
    file_name_list = [Path(fp).name for fp in file_paths]

    output = {
        "run_id": run_id,
        "status": status,
        "extraction_contract_version": "2.1",
        "summary": {
            "run_timestamp": datetime.utcnow().isoformat() + "Z",
            "total_documents": len(file_paths),
            "files_used": len(file_paths),
            "sources": source_inventory,
            "schema_inputs": schema_inputs,
            "tabs_populated": {k: (len(v) if isinstance(v, list) else len(v)) for k, v in tab_data.items()},
            "error_count": 0,
        },
        "company_identifiers": company_identifiers,
        "meta": {
            "mode": "multi_document",
            "files_used": len(file_paths),
            "source_documents": file_name_list,
        },
        "tab_data": tab_data,
        "auxiliary_data": auxiliary_data,
        "missing_fields": missing_fields,
        "input_completeness": input_completeness,
        "errors": [],
    }

    logger.info("=" * 55)
    logger.info("Status           : %s", status.upper())
    logger.info("Run ID           : %s", run_id)
    logger.info("Missing fields   : %s", missing_fields)
    logger.info("Agent3 readiness : %s", input_completeness["ready_for_analysis_from_agent1"])
    logger.info("Agent3 gaps      : %s", input_completeness["missing_from_agent1"])
    for tab, entries in tab_data.items():
        count = len(entries) if isinstance(entries, list) else len(entries)
        logger.info("  %-20s: %s item(s)", tab, count)
    logger.info("=" * 55)

    return output


async def main() -> None:
    """Standalone entry point: runs extraction and writes output to disk."""
    output = await run(DOCUMENTS_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = OUTPUT_DIR / "transformation_output.json"
    latest_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Output written to %s", latest_path)


if __name__ == "__main__":
    asyncio.run(main())
