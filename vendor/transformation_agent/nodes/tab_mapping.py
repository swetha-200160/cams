# nodes/tab_mapping.py
# ──────────────────────────────────────────────────────────────
# NODE 7 — Tab Mapping
#
# Pure Python — NO LLM calls. Routes structured financial data
# into the 4 UI display tabs:
#
#   overview          → company identifiers (name, industry, CIN, PAN)
#   balance_sheet     → ONE consolidated row per fiscal year
#   income_statement  → ONE consolidated row per fiscal year
#   cash_flow         → ONE consolidated row per fiscal year
#
# ── CONSOLIDATION (cross-document year merging) ─────────────────
# Problem: the original node appended EVERY row from EVERY document.
# 3 documents with FY2023 data produced 3 rows for FY2023.
#
# Fix — 3-step pipeline per tab:
#
#   Step 1: COLLECT
#     Gather all rows from all documents for each tab.
#     Tag each row with: source_document, doc_type, doc_priority.
#
#   Step 2: GROUP BY YEAR
#     Canonicalize year strings (FY2023 / 2022-23 / March 2023 → "2023").
#     All rows for the same canonical year form one group.
#
#   Step 3: MERGE
#     Each year group → ONE output row.
#     For each financial field:
#       - Only one document has a value → use it.
#       - Multiple documents, different values → highest-priority doc wins.
#       - Tied priority, different values → higher value wins.
#     source_documents = list of ALL contributing filenames for that year.
#
# ── COMPANY NAME: CONSENSUS VOTE ───────────────────────────────
# Problem: first-found logic picks the first non-null company_name,
# which may come from a cover page or embedded reference containing
# a wrong entity name (e.g. auditor firm name instead of borrower).
#
# Fix — _get_consensus_company():
#   Collects company_name from all structured_datasets.
#   Normalises to lowercase, filters out placeholder strings.
#   Picks the name that appears most frequently (majority vote).
#   Returns original casing from the first matching document.
#
# ── DOCUMENT TYPE PRIORITY ─────────────────────────────────────
# Used for conflict resolution when multiple documents provide a
# value for the same year and field. Lower number = higher priority.
#
#   Priority 1: Balance Sheet, Income Statement, Cash Flow Statement
#   Priority 2: Financial Statement (combined doc)
#   Priority 3: Annual Report
#   Priority 4: Bank Statement, GST Return, Income Tax Return
#   Priority 5: ROC Filing
#   Priority 7: Unknown
# ──────────────────────────────────────────────────────────────

import re
from collections import Counter
from typing import Optional
from state.agent_state import AgentState
from config import settings as _s

DOC_TYPE_PRIORITY           = _s.DOC_TYPE_PRIORITY
NON_FINANCIAL_DOC_TYPES     = _s.NON_FINANCIAL_DOC_TYPES
META_FIELDS                 = _s.META_FIELDS
BALANCE_SHEET_FIELDS        = _s.BALANCE_SHEET_FIELDS
INCOME_STMT_FIELDS          = _s.INCOME_STMT_FIELDS
CASH_FLOW_FIELDS            = _s.CASH_FLOW_FIELDS
RATIO_ANALYSIS_FIELDS       = _s.RATIO_ANALYSIS_FIELDS
INVALID_COMPANY_NAMES       = _s.INVALID_COMPANY_NAMES
RELIABLE_COMPANY_NAME_TYPES = _s.RELIABLE_COMPANY_NAME_TYPES
NAME_STOP_WORDS             = _s.NAME_STOP_WORDS

# Underscore-prefixed aliases for backward compatibility within this module
_NON_FINANCIAL_DOC_TYPES  = NON_FINANCIAL_DOC_TYPES
_META_FIELDS              = META_FIELDS
_BALANCE_SHEET_FIELDS     = BALANCE_SHEET_FIELDS
_INCOME_STMT_FIELDS       = INCOME_STMT_FIELDS
_CASH_FLOW_FIELDS         = CASH_FLOW_FIELDS
_RATIO_ANALYSIS_FIELDS    = RATIO_ANALYSIS_FIELDS
_INVALID_COMPANY_NAMES    = INVALID_COMPANY_NAMES


def tab_mapping_node(state: AgentState) -> AgentState:
    print("\n" + "─" * 55)
    print("🗂️   NODE 7 — Tab Mapping")
    print("─" * 55)

    errors = list(state.get("errors", []))

    tab_data = {
        "overview":             {},
        "balance_sheet":        [],
        "income_statement":     [],
        "cash_flow":            [],
        "ratio_analysis_data":  [],
        "bank_statements":      [],   # transaction-level rows from bank documents
        "gst_data":             None, # consolidated GST data (set after per-doc loop)
        "itr_data":             None, # consolidated ITR data (set after per-doc loop)
    }

    # Accumulates raw gst_data / itr_data dicts from docs before merging
    raw_gst_entries: list = []
    raw_itr_entries: list = []

    # All GST doc types (mirrors data_structuring.GST_DOC_TYPES)
    _GST_DOC_TYPES = {
        "GST Return",
        "GSTR-1 (Outward Supplies)",
        "GSTR-3B (Monthly Return)",
        "GSTR-9 (Annual Return)",
        "GSTR-9C (GST Audit)",
        "GSTR-2A (Auto-drafted Inward Supplies)",
        "GSTR-2B (Auto-drafted ITC Statement)",
    }

    # All ITR doc types (mirrors data_structuring.ITR_DOC_TYPES)
    _ITR_DOC_TYPES = {
        "Income Tax Return",
        "ITR-1 (Sahaj)", "ITR-2", "ITR-3", "ITR-4 (Sugam)",
        "ITR-5", "ITR-6", "ITR-7",
        "Form 26AS / AIS", "Form 16 (TDS Certificate)",
    }

    # ── Build doc_type lookup from classified_documents ───────
    doc_type_lookup = {
        doc["filename"]: doc.get("doc_type", "Unknown")
        for doc in state.get("classified_documents", [])
    }

    # ── cleaned_data: used as fallback for company name and identifiers ─
    # The cleaning LLM focuses on identifiers (company_name, industry, CIN, PAN)
    # while the structuring LLM focuses on financial fields — so cleaned_data
    # is more reliable for identifier lookup throughout this node.
    cleaned_data_state = state.get("cleaned_data", {})

    # ── Step 1: COLLECT all rows from all documents ───────────
    # Rows are tagged with doc_type and doc_priority so Step 3
    # (merge) can resolve conflicts by document authority.
    raw_balance_sheet    = []
    raw_income_statement = []
    raw_cash_flow        = []
    raw_ratio_analysis   = []

    for filename, data in state["structured_datasets"].items():
        if not isinstance(data, dict) or not data:
            print(f"   ⏭  {filename} — empty structured data, skipping")
            continue

        doc_type     = doc_type_lookup.get(filename, "Unknown")
        doc_priority = DOC_TYPE_PRIORITY.get(doc_type, 7)

        print(f"   📄 {filename}  [{doc_type}  priority={doc_priority}]")

        # ── Overview Tab ──────────────────────────────────────
        # company_name is excluded — resolved by consensus vote below.
        # For industry/cin/pan: check structured_datasets first, then
        # fall back to cleaned_data (which LLM extracted from raw text
        # and is more reliable for identifier fields than the structuring output).
        # Also skip if this doc has a company name that doesn't match consensus
        # — prevents IUCN industry from appearing in an InfoBeans output.
        for field in ["industry", "cin", "pan"]:
            if field in tab_data["overview"]:
                continue   # Already set — first valid source wins
            # Try structured_datasets value first
            val = data.get(field)
            # Fall back to cleaned_data if null
            if not val:
                val = (cleaned_data_state.get(filename) or {}).get(field)
            if val:
                tab_data["overview"][field] = val

        # ── Bank transaction collection ───────────────────────
        # Collect transaction rows from bank statement documents before
        # the non-financial skip so they are not lost.
        for txn in (data.get("bank_transactions") or []):
            if isinstance(txn, dict) and txn.get("amount") is not None:
                tab_data["bank_statements"].append({
                    **txn,
                    "source_document": filename,
                })

        # ── GST data collection ───────────────────────────────
        if doc_type in _GST_DOC_TYPES:
            gst = data.get("gst_data")
            if gst and isinstance(gst, dict):
                raw_gst_entries.append({**gst, "source_document": filename})

        # ── ITR data collection ───────────────────────────────
        if doc_type in _ITR_DOC_TYPES:
            itr = data.get("itr_data")
            if itr and isinstance(itr, dict):
                raw_itr_entries.append({**itr, "source_document": filename})

        # ── Skip non-financial doc types entirely ────────────────
        # These types cannot produce valid financial statement rows.
        # Their sections were already cleared by _enforce_doc_type_sections
        # in Node 6, but this is a second safety net at collection time.
        if doc_type in _NON_FINANCIAL_DOC_TYPES:
            print(f"      ⏭  Skipping financial row collection for [{doc_type}]")
            continue

        # ── Balance Sheet rows ─────────────────────────────────
        for row in (data.get("balance_sheets") or []):
            if isinstance(row, dict) and _has_financial_data(row, _BALANCE_SHEET_FIELDS):
                raw_balance_sheet.append({
                    **row,
                    "source_document": filename,
                    "doc_type":        doc_type,
                    "doc_priority":    doc_priority,
                })

        # ── Income Statement rows ─────────────────────────────
        for row in (data.get("income_statements") or []):
            if isinstance(row, dict) and _has_financial_data(row, _INCOME_STMT_FIELDS):
                raw_income_statement.append({
                    **row,
                    "source_document": filename,
                    "doc_type":        doc_type,
                    "doc_priority":    doc_priority,
                })

        # ── Cash Flow rows ────────────────────────────────────
        for row in (data.get("cash_flows") or []):
            if isinstance(row, dict) and _has_financial_data(row, _CASH_FLOW_FIELDS):
                raw_cash_flow.append({
                    **row,
                    "source_document": filename,
                    "doc_type":        doc_type,
                    "doc_priority":    doc_priority,
                })

        # ── Ratio Analysis rows ───────────────────────────────
        for row in (data.get("ratio_analysis_data") or []):
            if isinstance(row, dict) and _has_financial_data(row, _RATIO_ANALYSIS_FIELDS):
                raw_ratio_analysis.append({
                    **row,
                    "source_document": filename,
                    "doc_type":        doc_type,
                    "doc_priority":    doc_priority,
                })

    # ── Steps 2 + 3: GROUP BY YEAR → MERGE ───────────────────
    tab_data["balance_sheet"]       = _consolidate_by_year(raw_balance_sheet,    "balance_sheet")
    tab_data["income_statement"]    = _consolidate_by_year(raw_income_statement, "income_statement")
    tab_data["cash_flow"]           = _consolidate_by_year(raw_cash_flow,        "cash_flow")
    tab_data["ratio_analysis_data"] = _consolidate_by_year(raw_ratio_analysis,   "ratio_analysis_data")

    # ── GST consolidation ─────────────────────────────────────
    if raw_gst_entries:
        tab_data["gst_data"] = _consolidate_gst(raw_gst_entries)
        period = (tab_data["gst_data"] or {}).get("period", "?")
        print(f"   📊 GST data consolidated (period={period})")

    # ── ITR consolidation ─────────────────────────────────────
    if raw_itr_entries:
        tab_data["itr_data"] = _consolidate_itr(raw_itr_entries)
        period = (tab_data["itr_data"] or {}).get("period", "?")
        print(f"   📊 ITR data consolidated (period={period})")

    # ── Company name: consensus vote across all documents ─────
    # NOTE: Must run BEFORE the company mismatch filter (Step 4) so that
    # consensus_name is available when filtering rows by company name.
    # Weights by doc_type reliability. Uses cleaned_data as primary
    # source for company names — more reliable than structured_datasets
    # because the structuring LLM focuses on financial fields.
    consensus_name = _get_consensus_company(
        state["structured_datasets"],
        doc_type_lookup,
        cleaned_data_state,
    )
    if consensus_name:
        tab_data["overview"]["company_name"] = consensus_name
        print(f"\n   🏢 company_name resolved by consensus: {consensus_name}")
    else:
        tab_data["overview"]["company_name"] = None
        warn = (
            "Could not resolve company_name by consensus — "
            "all extractions were placeholders or conflicting"
        )
        print(f"\n   ⚠️  {warn}")
        errors.append(warn)

    # ── Step 4: COMPANY NAME MISMATCH FILTER ─────────────────
    # Remove rows from source documents whose company_name has no keyword
    # overlap with the consensus company name.
    # Uses cleaned_data (primary) + structured_datasets (fallback) for
    # company name lookup — structuring LLM often leaves company_name null
    # because it focuses on financial fields.
    for tab_name in ["balance_sheet", "income_statement", "cash_flow", "ratio_analysis_data"]:
        tab_data[tab_name] = _filter_wrong_company_rows(
            tab_data[tab_name],
            state.get("structured_datasets", {}),
            state.get("cleaned_data", {}),
            tab_data["overview"].get("company_name"),
            tab_name,
            errors,
        )

    # ── Step 5: SCALE OUTLIER FILTER ─────────────────────────
    # Remove rows whose numeric values are implausibly far (>= 1000x)
    # from the median of all values in that tab. Catches unit-scale
    # contamination (e.g. a document reporting in crores vs others
    # reporting in absolute rupees: 4.85 crores vs 9,820,900 rupees).
    for tab_name in ["balance_sheet", "income_statement", "cash_flow", "ratio_analysis_data"]:
        original      = tab_data[tab_name]
        filtered, out = _filter_scale_outliers(original, tab_name)
        tab_data[tab_name] = filtered
        for excluded in out:
            warn = (
                f"Scale outlier removed from {tab_name}: "
                f"year={excluded.get('year','?')} source={excluded.get('source_document','?')} "
                f"— values are 1000x out of scale with other rows."
            )
            print(f"   🔍 {warn}")
            errors.append(warn)

    # ── Step 6: BUILD OVERVIEW (summary figures) ─────────────
    # Derives 5 CAMS-required key metrics from the consolidated tabs.
    # Runs after all tab data is finalized and company_name is resolved.
    tab_data["overview"].update(
        _build_overview_metrics(
            tab_data["income_statement"],
            tab_data["balance_sheet"],
        )
    )

    # ── Log summary ───────────────────────────────────────────
    print(f"\n   {'─' * 48}")
    print(f"   📌 overview              : {len(tab_data['overview'])} fields")
    print(f"   📌 balance_sheet         : {len(tab_data['balance_sheet'])} consolidated year(s)")
    print(f"   📌 income_statement      : {len(tab_data['income_statement'])} consolidated year(s)")
    print(f"   📌 cash_flow             : {len(tab_data['cash_flow'])} consolidated year(s)")
    print(f"   📌 ratio_analysis_data   : {len(tab_data['ratio_analysis_data'])} consolidated year(s)")
    print(f"   📌 bank_statements       : {len(tab_data['bank_statements'])} transaction(s)")
    print(f"   📌 gst_data              : {'present' if tab_data['gst_data'] else 'absent'}")
    print(f"   📌 itr_data              : {'present' if tab_data['itr_data'] else 'absent'}")

    # Log which years are present in each financial tab
    for tab_name in ["balance_sheet", "income_statement", "cash_flow", "ratio_analysis_data"]:
        years = [r.get("year", "?") for r in tab_data[tab_name]]
        if years:
            print(f"      {tab_name} years: {years}")

    empty = [k for k, v in tab_data.items() if not v]
    if empty:
        warn = f"Tabs with no data: {empty}"
        print(f"\n   ⚠️  {warn}")
        errors.append(warn)

    return {
        **state,
        "tab_data": tab_data,
        "errors": errors,
        "current_step": "tab_mapping",
    }


# ══════════════════════════════════════════════════════════════
# COMPANY NAME CONSENSUS  (from uploaded file)
# ══════════════════════════════════════════════════════════════

_RELIABLE_COMPANY_NAME_TYPES = RELIABLE_COMPANY_NAME_TYPES


def _get_consensus_company(
    structured_datasets: dict,
    doc_type_lookup: dict = None,
    cleaned_data: dict = None,
) -> Optional[str]:
    """
    Vote across all documents to find the most agreed-upon company name.

    Company name source priority per document (most reliable first):
      1. cleaned_data[filename].company_name
         — the cleaning LLM focuses on identifiers (name, CIN, PAN)
      2. structured_datasets[filename].company_name
         — the structuring LLM focuses on financial fields; company_name
           is often null even when the cleaning LLM found it

    Voting: each valid name is weighted by doc_type reliability
    (_RELIABLE_COMPANY_NAME_TYPES). Financial docs (weight 3) outweigh
    Annual Reports and Company Profiles (weight 1) to prevent sample/
    template document contamination.
    """
    name_scores: dict = {}
    name_originals: dict = {}

    all_filenames = set(structured_datasets.keys()) | set((cleaned_data or {}).keys())

    for filename in all_filenames:
        # Primary: cleaned_data (cleaning LLM focuses on identifiers)
        name = (
            ((cleaned_data or {}).get(filename) or {}).get("company_name")
            or (structured_datasets.get(filename) or {}).get("company_name")
        )
        if not name or not isinstance(name, str):
            continue

        normalised = name.strip().lower()
        if not normalised or normalised in _INVALID_COMPANY_NAMES:
            continue

        doc_type = (doc_type_lookup or {}).get(filename, "Unknown")
        weight   = _RELIABLE_COMPANY_NAME_TYPES.get(doc_type, 1)

        name_scores[normalised]    = name_scores.get(normalised, 0) + weight
        name_originals[normalised] = name.strip()

    if not name_scores:
        return None

    best = max(name_scores, key=lambda k: name_scores[k])
    return name_originals.get(best)


# ══════════════════════════════════════════════════════════════
# CROSS-DOCUMENT CONSOLIDATION  (from previous version)
# ══════════════════════════════════════════════════════════════

def _consolidate_by_year(rows: list, tab_name: str) -> list:
    """
    Group all collected rows by canonical year and merge each group
    into a single output row.

    Args:
        rows     : All rows for one tab across all documents.
                   Each row carries doc_type, doc_priority, source_document.
        tab_name : Tab name used for logging only.

    Returns:
        List of merged rows — ONE row per canonical year, sorted ascending.
        Rows with unrecognizable year labels are appended at the end.
    """
    year_groups  = {}   # { canonical_year_str: [row, ...] }
    no_year_rows = []   # rows where year could not be canonicalized

    for row in rows:
        raw_year = row.get("year")
        if raw_year is None:
            no_year_rows.append(row)
            continue

        canon = _canonicalize_year(str(raw_year))
        if canon is None:
            no_year_rows.append(row)
            continue

        if canon not in year_groups:
            year_groups[canon] = []
        year_groups[canon].append(row)

    consolidated = []

    # sorted() gives ascending year order (string sort works for 4-digit years)
    for canon_year, group in sorted(year_groups.items()):
        if len(group) == 1:
            # Single document for this year — clean metadata and use directly
            consolidated.append(_clean_row(group[0], canon_year))
        else:
            # Multiple documents for the same year — merge by priority
            consolidated.append(_merge_year_group(canon_year, group, tab_name))

    # Rows that had no recognizable year — append at end without year change
    for row in no_year_rows:
        consolidated.append(_clean_row(row, row.get("year")))

    return consolidated


def _merge_year_group(canon_year: str, group: list, tab_name: str) -> dict:
    """
    Merge multiple document rows for the same canonical year into one row.

    Field-level merge rules (applied independently per field):
      1. Only one document has a non-null value → use it.
      2. Multiple documents, same value → use it once.
      3. Multiple documents, different values:
           a. Take the value from the highest-priority document
              (lowest DOC_TYPE_PRIORITY number).
           b. If tied on priority → take the HIGHER value.
              Financial statements report gross amounts; the higher
              value is less likely to be a truncated or summarized figure.

    source_documents: list of ALL filenames that contributed to this year.
    source_document:  kept as the primary source (backward compatibility).
    """
    # Sort by priority ascending so index 0 = highest-authority document
    group_sorted = sorted(group, key=lambda r: r.get("doc_priority", 7))

    # Collect all financial field names present across the entire group
    all_fields = set()
    for row in group:
        all_fields.update(k for k in row.keys() if k not in _META_FIELDS)

    merged = {"year": canon_year}

    for field in all_fields:
        # Collect (value, priority) for all rows that have a non-null value
        candidates = [
            (row.get(field), row.get("doc_priority", 7))
            for row in group_sorted
            if row.get(field) is not None
        ]

        if not candidates:
            merged[field] = None
        elif len(candidates) == 1:
            merged[field] = candidates[0][0]
        else:
            # Multiple non-null values — apply conflict resolution
            best_priority = candidates[0][1]  # group_sorted: index 0 = best
            same_priority_vals = [c[0] for c in candidates if c[1] == best_priority]

            if len(same_priority_vals) == 1:
                merged[field] = same_priority_vals[0]
            else:
                # Tied priority and different values → higher value wins
                try:
                    merged[field] = max(same_priority_vals)
                except TypeError:
                    merged[field] = same_priority_vals[0]

    # Deduplicate and record all contributing source filenames
    seen    = set()
    sources = []
    for row in group:
        src = row.get("source_document", "")
        if src and src not in seen:
            sources.append(src)
            seen.add(src)

    merged["source_documents"] = sources
    merged["source_document"]  = sources[0] if sources else ""

    return merged


def _clean_row(row: dict, canon_year) -> dict:
    """
    Produce a clean output row from a single-document row.
    - Sets year to the canonical form
    - Wraps source_document into a source_documents list
    - Strips internal merge-only fields (doc_type, doc_priority)
    """
    cleaned = {
        k: v for k, v in row.items()
        if k not in {"doc_type", "doc_priority", "source_documents"}
    }
    cleaned["year"]             = canon_year
    cleaned["source_documents"] = [row.get("source_document", "")]
    return cleaned


# ══════════════════════════════════════════════════════════════
# YEAR CANONICALIZATION  (self-contained — not imported from Node 5)
# ══════════════════════════════════════════════════════════════

def _canonicalize_year(raw: str) -> Optional[str]:
    """
    Normalize any year string format to a 4-digit calendar year string.

    Handles all formats found in Indian financial documents:
      "FY2023"           → "2023"
      "FY 2022"          → "2022"
      "2022-23"          → "2023"  (closing year of Indian fiscal year)
      "2022-2023"        → "2023"
      "2021-22"          → "2022"
      "March 2023"       → "2023"
      "Mar 2023"         → "2023"
      "31st March 2022"  → "2022"
      "Q1 FY2023"        → "2023"
      "Q3 2024"          → "2024"
      "2023"             → "2023"
      "AY2023-24"        → None    (assessment year — rejected)
      "", "null", "-"    → None
    """
    if not raw:
        return None

    text = raw.strip().lower()

    if text in ("null", "none", "na", "n/a", "-", ""):
        return None

    # Reject Income Tax assessment years — not financial reporting years
    if re.match(r"^ay\s*20\d{2}", text) or "assessment year" in text:
        return None

    # FY2023 / FY 2022
    m = re.match(r"^fy\s*(20\d{2})$", text)
    if m:
        return m.group(1)

    # Q[1-4] FY2023 / Q3 2024
    m = re.match(r"^q[1-4]\s*(?:fy)?\s*(20\d{2})$", text)
    if m:
        return m.group(1)

    # Indian fiscal year range: "2022-23" → "2023", "2022-2023" → "2023"
    m = re.match(r"^(20\d{2})[-/](20)?(\d{2})$", text)
    if m:
        start_year = int(m.group(1))
        suffix     = m.group(3)
        if len(suffix) == 2:
            closing = (start_year // 100) * 100 + int(suffix)
            if closing < start_year:   # century boundary (1999-00 → 2000)
                closing += 100
        else:
            closing = start_year + 1
        return str(closing)

    # "March 2023" / "Mar 2023" / "31st March 2022" etc.
    m = re.search(
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s+(20\d{2})\b",
        text
    )
    if m:
        return m.group(1)

    # Plain 4-digit year "2023"
    m = re.match(r"^(20\d{2})$", text)
    if m:
        return m.group(1)

    # Year anywhere in the string as last resort
    m = re.search(r"\b(20\d{2})\b", text)
    if m:
        return m.group(1)

    return None




# ══════════════════════════════════════════════════════════════
# COMPANY NAME MISMATCH FILTER
# ══════════════════════════════════════════════════════════════

_NAME_STOP_WORDS = NAME_STOP_WORDS


def _extract_name_keywords(name: str) -> set:
    """
    Extract meaningful keywords from a company name for comparison.
    Lowercases, splits on spaces and punctuation, removes stop words.
    """
    import re as _re
    tokens = _re.split(r"[\s,\.\-&/]+", name.lower().strip())
    return {t for t in tokens if t and t not in _NAME_STOP_WORDS and len(t) > 1}


def _filter_wrong_company_rows(
    rows: list,
    structured_datasets: dict,
    cleaned_data: dict,
    consensus_name: str,
    tab_name: str,
    errors: list,
) -> list:
    """
    Remove financial rows from source documents whose company_name is
    clearly a DIFFERENT company from the consensus.

    Company name lookup order (most reliable first):
      1. cleaned_data[filename].company_name
         — extracted by the cleaning LLM which focuses on identifiers
      2. structured_datasets[filename].company_name
         — extracted by the structuring LLM which focuses on financial fields
         — often null because the template prioritises financial data

    If both sources return null/empty, the document is kept (cannot judge).

    Comparison: keyword overlap between doc name and consensus name.
    Zero overlap AND both names have meaningful keywords → EXCLUDED.
    """
    if not consensus_name:
        return rows

    consensus_kw = _extract_name_keywords(consensus_name)
    if not consensus_kw:
        return rows

    kept = []
    excluded_docs = set()

    for row in rows:
        src_doc = row.get('source_document', '')

        # ── Lookup company name: cleaned_data first, structured_datasets fallback
        doc_company = (
            (cleaned_data.get(src_doc) or {}).get('company_name')
            or (structured_datasets.get(src_doc) or {}).get('company_name')
            or ''
        )

        if not doc_company:
            kept.append(row)   # Cannot judge without a company name
            continue

        # Treat LLM artefacts and generic placeholders as "no company name"
        if str(doc_company).strip().lower() in _INVALID_COMPANY_NAMES:
            kept.append(row)
            continue

        doc_kw  = _extract_name_keywords(str(doc_company))
        overlap = consensus_kw & doc_kw

        if not doc_kw:
            kept.append(row)
            continue

        if overlap:
            kept.append(row)
        else:
            if src_doc not in excluded_docs:
                warn = (
                    f"[{tab_name}] Excluded rows from '{src_doc}' — "
                    f"company name '{doc_company}' does not match "
                    f"consensus '{consensus_name}' (no keyword overlap)"
                )
                print(f"   🚫 {warn}")
                errors.append(warn)
                excluded_docs.add(src_doc)

    return kept


# ══════════════════════════════════════════════════════════════
# SCALE OUTLIER DETECTION
# ══════════════════════════════════════════════════════════════

def _filter_scale_outliers(rows: list, tab_name: str) -> tuple:
    """
    Remove rows whose numeric values are implausibly far from the
    median of all other values in the same tab.

    Problem this solves:
      When documents from different scale conventions are mixed
      (e.g. one in absolute rupees, another in crores), a value like
      4.85 (crores) appears alongside 9,820,900 (absolute) for the same
      field. The ratio is ~2,000,000x — clearly a unit mismatch.

    Strategy:
      1. Collect ALL non-null numeric values across ALL rows.
      2. Compute the median magnitude (log10 of absolute value).
      3. For each row, compute the median magnitude of its own values.
      4. If a row's median magnitude differs from the global median
         by >= 3 orders of magnitude (1000x), it is an outlier.
      5. Outlier rows are removed and returned separately for logging.

    Args:
        rows     : List of merged row dicts for one tab.
        tab_name : Tab name — used only to identify relevant fields.

    Returns:
        (kept_rows, excluded_rows)
    """
    import math

    if len(rows) < 3:
        return rows, []   # need at least 3 rows to determine scale reliably

    # ── Field lists per tab ───────────────────────────────────
    field_map = {
        "balance_sheet":       _BALANCE_SHEET_FIELDS,
        "income_statement":    _INCOME_STMT_FIELDS,
        "cash_flow":           _CASH_FLOW_FIELDS,
        "ratio_analysis_data": _RATIO_ANALYSIS_FIELDS,
    }
    fields = field_map.get(tab_name, [])

    def safe_log10(v):
        try:
            return math.log10(abs(float(v))) if float(v) != 0 else 0
        except Exception:
            return None

    # ── Collect all numeric magnitudes globally ───────────────
    all_magnitudes = []
    for row in rows:
        for f in fields:
            v = row.get(f)
            if v is not None:
                m = safe_log10(v)
                if m is not None:
                    all_magnitudes.append(m)

    if not all_magnitudes:
        return rows, []   # no numeric data to compare

    # Global median magnitude
    sorted_mags   = sorted(all_magnitudes)
    mid           = len(sorted_mags) // 2
    global_median = sorted_mags[mid]

    # ── Classify each row ─────────────────────────────────────
    kept     = []
    excluded = []

    for row in rows:
        row_magnitudes = []
        for f in fields:
            v = row.get(f)
            if v is not None:
                m = safe_log10(v)
                if m is not None:
                    row_magnitudes.append(m)

        if not row_magnitudes:
            kept.append(row)   # no numeric data → keep (nothing to compare)
            continue

        row_median = sorted(row_magnitudes)[len(row_magnitudes) // 2]

        # If this row's scale is >= 3 orders of magnitude away from global
        # (i.e. 1000x difference — e.g. 4.85 crores stored as "4.85"
        # vs 9,820,900 in absolute rupees).
        if abs(row_median - global_median) >= 3.0:
            excluded.append(row)
        else:
            kept.append(row)

    return kept, excluded


# ══════════════════════════════════════════════════════════════
# OVERVIEW METRICS BUILDER
# ══════════════════════════════════════════════════════════════

def _build_overview_metrics(income_rows: list, balance_rows: list) -> dict:
    """
    Derive the 5 CAMS Overview metrics from the consolidated tabs.
    Uses the MOST RECENT year available (last row after ascending sort).

    Overview fields:
      net_sales  -> income_statement.revenue_from_operations (latest year)
      ebitda     -> revenue + other_income - material - employee costs
      pat        -> ebitda - finance_cost - depreciation (approximation)
      networth   -> balance_sheet.share_capital + reserves_surplus
      total_debt -> long_term_borrowing + short_term_borrowing
    """
    metrics: dict = {}

    # Pick the income statement year with the MOST non-null fields.
    # Using the latest year fails when the newest row is a partial extract
    # (e.g. has costs but no revenue → EBITDA becomes wildly negative).
    # The most-complete row gives the most reliable Overview metrics.
    _IS_SCORE_FIELDS = [
        "revenue_from_operations", "other_income", "cost_of_material",
        "employee_benefit_expense", "finance_cost", "depreciation",
    ]
    def _row_completeness(row):
        return sum(1 for f in _IS_SCORE_FIELDS if row.get(f) is not None)

    if income_rows:
        max_score = max(_row_completeness(r) for r in income_rows)
        # Among most-complete rows, take the latest (last in ascending list)
        candidates = [r for r in income_rows if _row_completeness(r) == max_score]
        latest_is  = candidates[-1]
    else:
        latest_is = {}

    rev = latest_is.get("revenue_from_operations")
    oth = latest_is.get("other_income")
    mat = latest_is.get("cost_of_material")
    emp = latest_is.get("employee_benefit_expense")
    fin = latest_is.get("finance_cost")
    dep = latest_is.get("depreciation")

    if rev is not None:
        metrics["net_sales"] = rev

    # EBITDA approximation
    if any(v is not None for v in [rev, oth, mat, emp]):
        metrics["ebitda"] = (rev or 0) + (oth or 0) - (mat or 0) - (emp or 0)

    # PAT approximation = EBITDA - Finance Cost - Depreciation
    if "ebitda" in metrics and (fin is not None or dep is not None):
        metrics["pat"] = metrics["ebitda"] - (fin or 0) - (dep or 0)

    # Latest balance sheet row
    latest_bs = balance_rows[-1] if balance_rows else {}
    sc  = latest_bs.get("share_capital")
    res = latest_bs.get("reserves_surplus")
    ltb = latest_bs.get("long_term_borrowing")
    stb = latest_bs.get("short_term_borrowing")

    # Networth = Share Capital + Reserves & Surplus
    if sc is not None or res is not None:
        metrics["networth"] = (sc or 0) + (res or 0)

    # Total Debt = LT Borrowing + ST Borrowing
    if ltb is not None or stb is not None:
        metrics["total_debt"] = (ltb or 0) + (stb or 0)

    # Record which years the metrics are sourced from
    if latest_is.get("year"):
        metrics["metrics_year_income"]  = latest_is["year"]
    if latest_bs.get("year"):
        metrics["metrics_year_balance"] = latest_bs["year"]

    return metrics


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def _has_financial_data(row: dict, field_names: list) -> bool:
    """
    Return True if the row has at least one non-null value in the
    given list of financial field names.

    Uses a typed field_names list (not a generic metadata exclusion set)
    so non-financial string fields that happen to be non-null (e.g. a
    notes field) are never mistaken for financial data.

    Prevents storing skeleton rows where every financial field is None.
    """
    return any(row.get(f) is not None for f in field_names)


def _consolidate_gst(entries: list) -> dict:
    """
    Merge GST data from multiple documents (GSTR-1, GSTR-3B, GSTR-9, etc.)
    for the same period, then compute derived fields.

    Merge strategy:
      - Group by period (normalized to "FYXX-XX" form).
      - Within each period group, first-non-null wins per scalar field.
      - monthly_taxable_value: use the longest non-empty array found.
      - Use the most recently typed period group (latest FY) as the result
        if multiple periods exist; otherwise merge all into one block.

    Derived fields computed here (not by LLM):
      gst_consistency.difference, gst_consistency.match
      trend_analysis.*
      risk_flags.*
    """
    import math
    import statistics

    # ── Group entries by normalized period ────────────────────
    period_groups: dict = {}
    for entry in entries:
        period = entry.get("period") or "unknown"
        period_groups.setdefault(period, []).append(entry)

    # Pick the group to use: prefer most recent FY, fall back to "unknown"
    def _period_sort_key(p: str) -> str:
        # "FY25-26" → "2526", "unknown" → "0000"
        import re as _re
        m = _re.search(r"(\d{2,4})[-_](\d{2,4})", p)
        if m:
            return m.group(1).zfill(4) + m.group(2).zfill(4)
        return "0000"

    best_period = max(period_groups.keys(), key=_period_sort_key)
    group = period_groups[best_period]

    # ── Merge scalar fields (first-non-null wins) ─────────────
    scalar_fields = [
        "period", "annual_taxable_value",
        "igst", "cgst", "sgst", "total_tax_paid",
        "gstr1_total_sales", "gstr3b_total_sales",
        "b2b_sales", "export_sales", "domestic_sales",
    ]
    merged: dict = {}
    for field in scalar_fields:
        for entry in group:
            val = entry.get(field)
            if val is not None:
                merged[field] = val
                break
        else:
            merged[field] = None

    # ── monthly_taxable_value: longest non-empty list ─────────
    best_monthly: list = []
    for entry in group:
        monthly = entry.get("monthly_taxable_value") or []
        if isinstance(monthly, list) and len(monthly) > len(best_monthly):
            best_monthly = monthly
    merged["monthly_taxable_value"] = best_monthly

    # ── Compute annual from monthly if null ───────────────────
    if merged["annual_taxable_value"] is None and best_monthly:
        total = sum(m.get("value") or 0 for m in best_monthly if isinstance(m, dict))
        if total > 0:
            merged["annual_taxable_value"] = total

    # ── Consistency ───────────────────────────────────────────
    g1 = merged.get("gstr1_total_sales")
    g3b = merged.get("gstr3b_total_sales")
    if g1 is not None and g3b is not None:
        diff = round(g1 - g3b, 2)
        match = diff == 0
    elif g1 is not None or g3b is not None:
        # If only one is available, use annual_taxable_value as the other
        ann = merged.get("annual_taxable_value")
        if g1 is None and ann is not None:
            g1 = ann
        if g3b is None and ann is not None:
            g3b = ann
        diff = round((g1 or 0) - (g3b or 0), 2)
        match = diff == 0
    else:
        diff = None
        match = None

    # ── Trend analysis from monthly data ──────────────────────
    monthly_values = [
        float(m["value"])
        for m in best_monthly
        if isinstance(m, dict) and m.get("value") is not None
    ]

    trend: dict = {}
    if monthly_values:
        avg = sum(monthly_values) / len(monthly_values)
        trend["average_monthly_sales"] = round(avg)

        max_val = max(monthly_values)
        min_val = min(monthly_values)
        max_idx = monthly_values.index(max_val)
        min_idx = monthly_values.index(min_val)

        trend["highest_month"] = {
            "month":  best_monthly[max_idx].get("month"),
            "value":  max_val,
        }
        trend["lowest_month"] = {
            "month":  best_monthly[min_idx].get("month"),
            "value":  min_val,
        }

        # Volatility: coefficient of variation (std_dev / mean)
        if len(monthly_values) > 1:
            std_dev = statistics.stdev(monthly_values)
            cv = std_dev / avg if avg > 0 else 0.0
            if cv < 0.10:
                trend["sales_volatility"] = "low"
            elif cv < 0.25:
                trend["sales_volatility"] = "moderate"
            else:
                trend["sales_volatility"] = "high"

            # Growth pattern: compare first-half avg vs second-half avg
            mid = len(monthly_values) // 2
            first_half_avg = sum(monthly_values[:mid]) / mid if mid > 0 else avg
            second_half_avg = sum(monthly_values[mid:]) / (len(monthly_values) - mid) if (len(monthly_values) - mid) > 0 else avg
            growth_rate = (second_half_avg - first_half_avg) / first_half_avg if first_half_avg > 0 else 0.0
            if growth_rate > 0.05:
                trend["growth_pattern"] = "growing"
            elif growth_rate < -0.05:
                trend["growth_pattern"] = "declining"
            else:
                trend["growth_pattern"] = "stable"
        else:
            trend["sales_volatility"] = "insufficient_data"
            trend["growth_pattern"]   = "insufficient_data"

    # ── Risk flags ────────────────────────────────────────────
    risk: dict = {
        "gstr_mismatch":           not match if match is not None else False,
        "abnormal_spike_detected": False,
        "sudden_drop_detected":    False,
        "tax_inconsistency":       False,
    }

    if len(monthly_values) >= 3 and trend.get("average_monthly_sales"):
        avg    = trend["average_monthly_sales"]
        std_dev_v = statistics.stdev(monthly_values) if len(monthly_values) > 1 else 0

        # Abnormal spike: any month > mean + 2σ
        if std_dev_v > 0:
            risk["abnormal_spike_detected"] = any(v > avg + 2 * std_dev_v for v in monthly_values)

        # Sudden drop: any consecutive pair drops > 30%
        for i in range(1, len(monthly_values)):
            prev = monthly_values[i - 1]
            curr = monthly_values[i]
            if prev > 0 and (prev - curr) / prev > 0.30:
                risk["sudden_drop_detected"] = True
                break

    # Tax inconsistency: total_tax_paid does not match igst+cgst+sgst (within 1%)
    igst  = merged.get("igst")  or 0
    cgst  = merged.get("cgst")  or 0
    sgst  = merged.get("sgst")  or 0
    total = merged.get("total_tax_paid")
    if total and (igst + cgst + sgst) > 0:
        expected = igst + cgst + sgst
        if abs(total - expected) / expected > 0.01:
            risk["tax_inconsistency"] = True

    # ── Assemble final gst_data structure ─────────────────────
    return {
        "period": merged.get("period") or best_period,
        "gst_sales": {
            "monthly_taxable_value": best_monthly,
            "annual_taxable_value":  merged.get("annual_taxable_value"),
        },
        "gst_tax": {
            "igst":           merged.get("igst"),
            "cgst":           merged.get("cgst"),
            "sgst":           merged.get("sgst"),
            "total_tax_paid": merged.get("total_tax_paid"),
        },
        "gst_consistency": {
            "gstr1_total_sales":  g1,
            "gstr3b_total_sales": g3b,
            "difference":         diff,
            "match":              match,
        },
        "sales_breakdown": {
            "b2b_sales":      merged.get("b2b_sales"),
            "export_sales":   merged.get("export_sales"),
            "domestic_sales": merged.get("domestic_sales"),
        },
        "trend_analysis":  trend,
        "risk_flags":      risk,
    }


def _consolidate_itr(entries: list) -> dict:
    """
    Merge ITR data from multiple ITR documents for the most recent period,
    then compute compliance_flags in code.

    Merge strategy:
      - Group by period; pick most recent.
      - Within the group, first-non-null wins for each scalar field.

    Computed here (not by LLM):
      compliance_flags.tax_fully_paid   → net_payable <= 0
      compliance_flags.disallowances_present → reported_total_income > taxable_income
      compliance_flags.filing_delay     → filed_on_time is False
    """
    # ── Group by period, pick most recent ─────────────────────
    period_groups: dict = {}
    for entry in entries:
        period = entry.get("period") or "unknown"
        period_groups.setdefault(period, []).append(entry)

    def _period_sort_key(p: str) -> str:
        import re as _re
        m = _re.search(r"(\d{2,4})[-_](\d{2,4})", p)
        if m:
            return m.group(1).zfill(4) + m.group(2).zfill(4)
        return "0000"

    best_period = max(period_groups.keys(), key=_period_sort_key)
    group = period_groups[best_period]

    # ── Merge scalar fields (first-non-null wins) ─────────────
    scalar_fields = [
        "period", "itr_form", "section", "filed_on_time",
        "audit_applicable", "audit_completed",
        "reported_total_income", "taxable_income",
        "total_receipts", "tds_deducted",
        "total_liability", "tds_credit", "advance_tax", "net_payable",
    ]
    merged: dict = {}
    for field in scalar_fields:
        for entry in group:
            val = entry.get(field)
            if val is not None:
                merged[field] = val
                break
        else:
            merged[field] = None

    # ── Compute compliance_flags ──────────────────────────────
    net_payable            = merged.get("net_payable")
    reported_total_income  = merged.get("reported_total_income")
    taxable_income         = merged.get("taxable_income")
    filed_on_time          = merged.get("filed_on_time")

    tax_fully_paid = (net_payable is not None and float(net_payable) <= 0)

    disallowances_present = (
        reported_total_income is not None
        and taxable_income is not None
        and float(reported_total_income) > float(taxable_income)
    )

    filing_delay = (filed_on_time is False)

    # ── Assemble final itr_data structure ─────────────────────
    return {
        "period": merged.get("period") or best_period,
        "filing_status": {
            "section":        merged.get("section"),
            "filed_on_time":  merged.get("filed_on_time"),
            "audit_applicable": merged.get("audit_applicable"),
            "audit_completed":  merged.get("audit_completed"),
        },
        "income": {
            "reported_total_income": merged.get("reported_total_income"),
            "taxable_income":        merged.get("taxable_income"),
        },
        "tds": {
            "total_receipts": merged.get("total_receipts"),
            "tds_deducted":   merged.get("tds_deducted"),
        },
        "tax": {
            "total_liability": merged.get("total_liability"),
            "tds_credit":      merged.get("tds_credit"),
            "advance_tax":     merged.get("advance_tax"),
            "net_payable":     merged.get("net_payable"),
        },
        "compliance_flags": {
            "tax_fully_paid":         tax_fully_paid,
            "disallowances_present":  disallowances_present,
            "filing_delay":           filing_delay,
        },
    }


def _sort_by_year(rows: list) -> list:
    """
    Sort a list of row dicts by the 'year' field ascending.
    Rows without a year field are placed at the end.
    Handles string years like '2023', '2024-25', None gracefully.

    NOTE: _consolidate_by_year() already produces sorted output via
    sorted(year_groups.items()). This function is retained for any
    caller that may need standalone sorting of an unsorted row list.
    """
    def year_key(row):
        year = row.get("year")
        if year is None:
            return "9999"
        return str(year)

    return sorted(rows, key=year_key)