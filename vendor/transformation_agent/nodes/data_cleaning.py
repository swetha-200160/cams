# nodes/data_cleaning.py
# ──────────────────────────────────────────────────────────────
# NODE 5 — Data Cleaning & Normalization
#
# FIX: Now consumes state["extracted_tables"] alongside extracted text.
# This closes the critical gap where Node 4's structured table data
# was stored but never used.
#
# Input strategy (updated):
#   1. Text sections from preprocess_document() (max 2000 chars)
#   2. Serialized table rows from extracted_tables (max 1500 chars)
#   Both are combined into a single prompt block so the LLM can cross-
#   reference table cell values against section headers for accuracy.
#
# Why tables matter more than raw text for financials:
#   A balance sheet table already has headers (Year / Assets / Liabilities)
#   and values in aligned columns. Sending the LLM those structured rows
#   yields far more reliable numeric extraction than parsing markdown prose
#   where the same numbers appear in flowing sentences.
#
# ── NEW: DUPLICATE HANDLING & NORMALIZATION ─────────────────────
# After the LLM returns raw JSON, three post-processing steps run:
#
#   Step A — Merge Node 4 table insights
#     Node 4 already extracted structured financial values from tables
#     into financial_insights.extracted_values. These are MORE reliable
#     than LLM re-extraction (they come from exact cell values, not text).
#     Table insight values WIN over LLM values when both exist for the
#     same field + year. LLM values fill in years/fields not in tables.
#
#   Step B — Year canonicalization
#     All year strings are normalized to 4-digit calendar year strings:
#       "FY2023"      → "2023"
#       "2022-23"     → "2023"  (Indian fiscal year — take closing year)
#       "March 2023"  → "2023"
#       "Q3 FY2024"   → "2024"
#     Entries with unrecognizable year labels are dropped.
#
#   Step C — Deduplication & conflict resolution
#     After canonicalization, years_found is deduplicated (sorted).
#     Each figure array (revenue, profit, asset, liability, equity,
#     cash_flow) is deduplicated by canonical year:
#       - Identical values: keep one entry silently.
#       - Conflicting values: HIGHER value wins (financial statements
#         typically report gross figures; higher = less likely truncated).
#         Conflict is logged to errors for traceability.
#     Zero-value entries and null-year entries are removed.
#
# Fallback:
#   If both text and tables are empty, skip.
#   If LLM returns malformed JSON, store { raw_text: ... } and continue.
# ──────────────────────────────────────────────────────────────

import json
import re
from typing import Optional
from langchain.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

from tools.llm_client import get_chat_llm
from tools.docling_reader import parse_document
from config import settings as _s
from config.settings import MAX_PROMPT_CHARS
from state.agent_state import AgentState

CLEANING_MAX_TABLE_CHARS = _s.CLEANING_MAX_TABLE_CHARS
CLEANING_MAX_TEXT_CHARS  = _s.CLEANING_MAX_TEXT_CHARS
CLEANING_HEADER_CHARS    = _s.CLEANING_HEADER_CHARS
CLEANING_CF_CHARS        = _s.CLEANING_CF_CHARS
NODE4_TO_CLEANING_FIELD  = _s.NODE4_TO_CLEANING_FIELD
NODE4_FIELD_PRIORITY     = _s.NODE4_FIELD_PRIORITY
CF_ANCHOR_PATTERNS       = _s.CF_ANCHOR_PATTERNS
CLEANING_FIGURE_ARRAYS   = _s.CLEANING_FIGURE_ARRAYS


def split_text_safely(text, max_chars=2000):
    """
    Split text into semantic chunks (paragraph-aware).
    Avoids breaking sentences randomly.
    """
    if not text:
        return []

    paragraphs = text.split("\n\n")
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) < max_chars:
            current += para + "\n\n"
        else:
            if current:
                chunks.append(current.strip())
            current = para + "\n\n"

    if current:
        chunks.append(current.strip())

    return chunks


def merge_chunk_results(results):
    """
    Merge multiple LLM outputs safely.
    """
    merged = {}

    for r in results:
        if not isinstance(r, dict):
            continue

        for key, value in r.items():
            if value in (None, "", [], {}):
                continue

            if key not in merged:
                # Copy lists to avoid mutating the original result dict
                merged[key] = list(value) if isinstance(value, list) else value
            else:
                # Merge list fields (financial arrays)
                if isinstance(value, list):
                    merged[key] = merged[key] + value  # new list, no mutation

                # Prefer non-null string (first non-empty wins)
                elif isinstance(value, str):
                    if not merged[key]:
                        merged[key] = value

    return merged



MAX_TABLE_CHARS = CLEANING_MAX_TABLE_CHARS  # loaded from config.yaml
MAX_TEXT_CHARS  = CLEANING_MAX_TEXT_CHARS   # loaded from config.yaml


# ── Cleaning & Extraction prompt ──────────────────────────────
# Updated to accept both {text} and {tables} inputs.
# LLM is instructed to prefer table values when text and tables conflict,
# since tables are already structured and less prone to OCR drift.
CLEAN_PROMPT = PromptTemplate.from_template(
    """You are a financial data extraction expert for a bank.

Extract information from the sources below and return ONLY a JSON object.
Do NOT include any explanation, markdown, or code fences.
- Do NOT extract incorporation years or founding years as financial reporting years
- "Assessment Year YYYY-YY" labels are NOT financial years — ignore them
- years_found must be an empty list [] if no valid financial reporting year is found — never use the string "null"
IMPORTANT: If table data is provided, prefer table values over text for numbers.
Tables have already been parsed and are more reliable than raw text.

JSON schema to fill:
{{
  "company_name": "string or null",
  "industry": "string or null",
  "cin": "string or null",
  "pan": "string or null",
  "years_found": ["list of year strings like 2023"],

  "net_sales_figures":      [{{"year": "2023", "value": 0}}],
  "other_income_figures":   [{{"year": "2023", "value": 0}}],
  "material_cost_figures":  [{{"year": "2023", "value": 0}}],
  "employee_cost_figures":  [{{"year": "2023", "value": 0}}],
  "finance_cost_figures":   [{{"year": "2023", "value": 0}}],
  "depreciation_figures":   [{{"year": "2023", "value": 0}}],
  "share_capital_figures":  [{{"year": "2023", "value": 0}}],
  "reserves_figures":       [{{"year": "2023", "value": 0}}],
  "lt_borrowing_figures":   [{{"year": "2023", "value": 0}}],
  "st_borrowing_figures":   [{{"year": "2023", "value": 0}}],
  "trade_payables_figures": [{{"year": "2023", "value": 0}}],
  "fixed_assets_figures":   [{{"year": "2023", "value": 0}}],
  "operating_cf_figures":   [{{"year": "2023", "value": 0}}],
  "investing_cf_figures":   [{{"year": "2023", "value": 0}}],
  "financing_cf_figures":   [{{"year": "2023", "value": 0}}],
  "net_cash_figures":       [{{"year": "2023", "value": 0}}],

  "total_revenue_figures":              [{{"year": "2023", "value": 0}}],
  "net_operating_income_figures":       [{{"year": "2023", "value": 0}}],
  "operating_expense_figures":          [{{"year": "2023", "value": 0}}],
  "other_operating_expense_figures":    [{{"year": "2023", "value": 0}}],
  "ebitda_figures":                     [{{"year": "2023", "value": 0}}],
  "profit_before_tax_figures":          [{{"year": "2023", "value": 0}}],
  "current_tax_figures":                [{{"year": "2023", "value": 0}}],
  "deferred_tax_figures":               [{{"year": "2023", "value": 0}}],
  "net_income_figures":                 [{{"year": "2023", "value": 0}}],
  "interest_payment_figures":           [{{"year": "2023", "value": 0}}],
  "principal_repayment_figures":        [{{"year": "2023", "value": 0}}],
  "total_debt_service_figures":         [{{"year": "2023", "value": 0}}],
  "fixed_payment_obligation_figures":   [{{"year": "2023", "value": 0}}],
  "opening_debt_figures":               [{{"year": "2023", "value": 0}}],
  "closing_debt_figures":               [{{"year": "2023", "value": 0}}],
  "current_assets_figures":             [{{"year": "2023", "value": 0}}],
  "current_liabilities_figures":        [{{"year": "2023", "value": 0}}],
  "shareholder_equity_figures":         [{{"year": "2023", "value": 0}}],

  "notes": "any important observations or null"
}}

Rules:
- Use null for any field not found in either source
- Standardize: Net Sales/Turnover/Revenue from Operations → net_sales_figures
- Standardize: Total Revenue (incl. other income) → total_revenue_figures
- Standardize: Raw Material/COGS/Cost of Goods Sold → material_cost_figures
- Standardize: Staff Cost/Salaries/Personnel Expenses → employee_cost_figures
- Standardize: Total Operating Expenses → operating_expense_figures
- Standardize: Other Operating Expenses (excl. COGS, employee) → other_operating_expense_figures
- Standardize: Interest/Finance Charges/Borrowing Costs → finance_cost_figures
- Standardize: Interest Paid/Interest Expense → interest_payment_figures
- Standardize: Principal Repayment/Loan Repayment → principal_repayment_figures
- Standardize: Debt Service/Total Debt Service → total_debt_service_figures
- Standardize: Fixed Charges/Lease Payments/Fixed Obligations → fixed_payment_obligation_figures
- Standardize: Opening Loan Balance/Opening Borrowings → opening_debt_figures
- Standardize: Closing Loan Balance/Closing Borrowings → closing_debt_figures
- Standardize: Depreciation & Amortisation/D&A → depreciation_figures
- Standardize: EBITDA/Earnings Before Interest Tax D&A → ebitda_figures
- Standardize: Profit Before Tax/PBT/EBIT → profit_before_tax_figures
- Standardize: Current Tax/Income Tax Expense → current_tax_figures
- Standardize: Deferred Tax/(Credit) → deferred_tax_figures
- Standardize: Net Profit/PAT/Profit After Tax/Net Income → net_income_figures
- Standardize: Net Operating Income/Operating Profit → net_operating_income_figures
- Standardize: Reserves & Surplus/Retained Earnings → reserves_figures
- Standardize: Term Loans/Long Term Debt → lt_borrowing_figures
- Standardize: Working Capital/CC/OD/Short Term Debt → st_borrowing_figures
- Standardize: Current Assets/Total Current Assets → current_assets_figures
- Standardize: Current Liabilities/Total Current Liabilities → current_liabilities_figures
- Standardize: Shareholder Equity/Total Equity/Net Worth → shareholder_equity_figures
- Standardize: Cash from Operations → operating_cf_figures
- Standardize: "A. Cash flow from Operating Activities" / "Net cash from operating" → operating_cf_figures
- Standardize: "B. Cash flow from Investing Activities" / "Net cash from investing" → investing_cf_figures
- Standardize: "C. Cash flow from Financing Activities" / "Net cash from financing" → financing_cf_figures
- Standardize: "Net increase/decrease in cash" / "Closing cash balance" → net_cash_figures
- Cash flow tables use row labels — look for section totals, not individual line items
- Convert: 1 lakh = 100000, 1 crore = 10000000
- Return ONLY the JSON object, nothing else

--- EXTRACTED TABLE DATA ---
{tables}

--- TEXT SECTIONS ---
{text}

JSON:"""
)


# ══════════════════════════════════════════════════════════════
# NODE 4 FIELD → CLEANING SCHEMA MAPPING
# ──────────────────────────────────────────────────────────────
# Node 4 stores financial_insights.extracted_values with its own
# canonical field keys (e.g. "revenue", "net_profit", "total_assets").
# This mapping translates those keys into Node 5's figure array names.
#
# Priority within a group: first key listed wins when multiple
# Node 4 fields map to the same cleaning figure array.
# (e.g. "net_profit" is preferred over "ebit" for profit_figures)
# ══════════════════════════════════════════════════════════════

# NODE4_TO_CLEANING_FIELD and NODE4_FIELD_PRIORITY loaded from config.yaml via settings.


# ══════════════════════════════════════════════════════════════
# NODE FUNCTION
# ══════════════════════════════════════════════════════════════

def data_cleaning_node(state: AgentState) -> AgentState:
    print("\n" + "─" * 55)
    print("🧹  NODE 5 — Data Cleaning & Normalization")
    print("─" * 55)

    llm    = get_chat_llm()
    chain  = CLEAN_PROMPT | llm | StrOutputParser()
    errors = list(state.get("errors", []))
    cleaned_data = {}

    for doc in state["classified_documents"]:
        filename = doc["filename"]
        filepath = doc["filepath"]
        raw_text = state["extracted_texts"].get(filename, "")

        # ── Pull extracted tables for this document ───────────
        doc_tables = state.get("extracted_tables", {}).get(filename, [])

        has_text   = bool(raw_text.strip())
        has_tables = bool(doc_tables)

        if not has_text and not has_tables:
            print(f"   ⏭  {filename} — no text or tables, skipping")
            cleaned_data[filename] = {}
            continue

        # ── Build text context (sections preferred) ───────────
        text_context  = _build_text_context(filepath, raw_text)

        # ── Build table context ───────────────────────────────
        table_context = _build_table_context(doc_tables)

        if has_tables:
            print(f"   📊 {filename} — using {len(doc_tables)} table(s) + text")
        else:
            print(f"   📝 {filename} — text only (no tables)")

        # ── LLM cleaning call ─────────────────────────────────
        try:
            # ── SAFE CHUNKING IMPLEMENTATION ─────────────────────

            text_chunks = split_text_safely(text_context, max_chars=2000)

            # Fallback if no chunks
            if not text_chunks:
                text_chunks = [text_context]

            all_results = []

            for i, chunk in enumerate(text_chunks):
                try:
                    print(f"      🔹 Processing chunk {i+1}/{len(text_chunks)}")

                    # Tables are only sent with the first chunk.
                    # They contain the reliable numeric data (cell values).
                    # Subsequent chunks focus on text-only extraction
                    # (e.g. company identifiers buried later in the document).
                    # Sending tables to every chunk would duplicate all
                    # numeric figures N times — handled by dedup but wasteful.
                    chunk_tables = table_context if i == 0 else "No tables."

                    response = chain.invoke({
                        "text":   chunk,
                        "tables": chunk_tables,
                    })

                    parsed = _safe_parse_json(response)
                    all_results.append(parsed)

                except Exception as e:
                    err = f"{filename} chunk {i+1} failed: {e}"
                    print(f"      ⚠️  {err}")
                    errors.append(err)

            # Merge all chunk outputs
            cleaned = merge_chunk_results(all_results)

            # ── POST-PROCESSING ────────────────────────────────
            file_errors = []

            cleaned = _merge_table_insights(cleaned, doc_tables, file_errors)
            cleaned = _run_deduplication(cleaned, filename, file_errors)

            errors.extend(file_errors)

            cleaned_data[filename] = cleaned
            print(f"   ✅ {filename}  →  cleaned & deduplicated OK")
            _log_extracted_fields(cleaned)

        except Exception as e:
            err = f"Data cleaning LLM call failed for {filename}: {e}"
            print(f"   ❌ {err}")
            errors.append(err)
            cleaned_data[filename] = {"raw_text": raw_text[:500]}

    return {
        **state,
        "cleaned_data": cleaned_data,
        "errors": errors,
        "current_step": "data_cleaning",
    }


# ══════════════════════════════════════════════════════════════
# STEP A — MERGE NODE 4 TABLE INSIGHTS
# ══════════════════════════════════════════════════════════════

def _merge_table_insights(cleaned: dict, doc_tables: list, errors: list) -> dict:
    """
    Merge Node 4's financial_insights.extracted_values into the LLM output.

    Node 4 extracts values from structured table cells directly — no LLM,
    no text parsing — making them more reliable than the LLM's re-extraction.
    This function uses them as authoritative ground truth:

      - If Node 4 has a value for field + year and LLM also has one:
          → Node 4's value WINS (overrides LLM).
      - If Node 4 has a field + year that LLM missed:
          → Node 4's value is ADDED as a new entry.
      - If LLM has a field + year that Node 4 missed:
          → LLM's value is KEPT (Node 4 didn't cover that field/year).

    Only financial tables (is_financial_table=True) contribute values.
    Tables without financial_insights are silently skipped.
    """
    if not doc_tables:
        return cleaned

    # Collect all Node 4 extracted values across all financial tables
    # { figure_array_name: { canonical_year: value } }
    table_ground_truth: dict = {}

    for table in doc_tables:
        insights = table.get("financial_insights", {})
        if not insights.get("is_financial_table"):
            continue

        extracted = insights.get("extracted_values", {})

        # Process fields in priority order (net_profit before ebitda, etc.)
        for node4_field in NODE4_FIELD_PRIORITY:
            if node4_field not in extracted:
                continue

            target_array = NODE4_TO_CLEANING_FIELD.get(node4_field)
            if not target_array:
                continue

            year_values = extracted[node4_field]  # { "FY2023": 7400000, ... }

            for raw_year, value in year_values.items():
                canon_year = _canonicalize_year(str(raw_year))
                if canon_year is None:
                    continue
                if value is None or not isinstance(value, (int, float)):
                    continue

                # Only set if not already set by a higher-priority Node 4 field
                if target_array not in table_ground_truth:
                    table_ground_truth[target_array] = {}
                if canon_year not in table_ground_truth[target_array]:
                    table_ground_truth[target_array][canon_year] = value

    if not table_ground_truth:
        return cleaned  # No Node 4 insights to merge

    # Merge into cleaned dict — Node 4 values override LLM values
    for figure_array, year_value_map in table_ground_truth.items():
        existing_entries = cleaned.get(figure_array) or []

        # Build a lookup of existing LLM entries by canonical year
        existing_by_year = {}
        for entry in existing_entries:
            if not isinstance(entry, dict):
                continue
            raw_yr = str(entry.get("year", "") or "")
            canon  = _canonicalize_year(raw_yr)
            if canon:
                existing_by_year[canon] = entry

        # Merge Node 4 values in
        for canon_year, node4_value in year_value_map.items():
            if canon_year in existing_by_year:
                llm_value = existing_by_year[canon_year].get("value")
                if llm_value != node4_value:
                    # Node 4 wins — update the entry
                    existing_by_year[canon_year]["value"] = node4_value
                    existing_by_year[canon_year]["year"]  = canon_year
            else:
                # New year not found by LLM — add it
                existing_by_year[canon_year] = {
                    "year":  canon_year,
                    "value": node4_value,
                }

        cleaned[figure_array] = list(existing_by_year.values())

    return cleaned


# ══════════════════════════════════════════════════════════════
# STEPS B + C — YEAR CANONICALIZATION & DEDUPLICATION
# ══════════════════════════════════════════════════════════════

def _run_deduplication(cleaned: dict, filename: str, errors: list) -> dict:
    """
    Orchestrates Steps B and C for all figure arrays and years_found.

    B — Canonicalize all year strings to 4-digit format.
    C — Deduplicate entries by canonical year. Resolve conflicts.
        Remove zero-value and null-year entries.
    """
    FIGURE_ARRAYS = CLEANING_FIGURE_ARRAYS   # loaded from config.yaml

    # ── Step B+C for all figure arrays ────────────────────────
    for array_name in FIGURE_ARRAYS:
        raw_entries = cleaned.get(array_name)
        if not raw_entries or not isinstance(raw_entries, list):
            cleaned[array_name] = []
            continue

        cleaned[array_name] = _deduplicate_figures(
            raw_entries, array_name, filename, errors
        )

    # ── Step B+C for years_found ───────────────────────────────
    raw_years = cleaned.get("years_found")
    if raw_years and isinstance(raw_years, list):
        canon_years = set()
        for y in raw_years:
            c = _canonicalize_year(str(y))
            if c:
                canon_years.add(c)
        cleaned["years_found"] = sorted(canon_years)
    else:
        cleaned["years_found"] = []

    # ── Rebuild years_found from figure arrays if empty ────────
    # If the LLM returned an empty years_found but we have year data
    # in the figure arrays, derive years_found from those entries.
    if not cleaned["years_found"]:
        derived_years = set()
        for array_name in FIGURE_ARRAYS:
            for entry in (cleaned.get(array_name) or []):
                yr = entry.get("year")
                if yr:
                    derived_years.add(str(yr))
        if derived_years:
            cleaned["years_found"] = sorted(derived_years)

    return cleaned


def _deduplicate_figures(
    entries: list, array_name: str, filename: str, errors: list
) -> list:
    """
    Deduplicate a figure array by canonical year.

    Processing order:
      1. Skip entries with null/empty year fields.
      2. Canonicalize year string.
      3. Skip entries with non-canonicalizable years (e.g. "AY2023-24").
      4. Skip entries with zero or null values.
      5. For duplicate canonical years:
           - Same value → keep one silently.
           - Different values → HIGHER value wins. Log conflict warning.

    Returns a list sorted ascending by canonical year.
    """
    # { canonical_year: float value }
    best: dict = {}

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        raw_year = entry.get("year")
        value    = entry.get("value")

        # ── Filter: null/missing year ─────────────────────────
        if raw_year is None or str(raw_year).strip() in ("", "null", "None"):
            continue

        # ── Step B: Canonicalize year ─────────────────────────
        canon_year = _canonicalize_year(str(raw_year))
        if canon_year is None:
            continue   # unrecognizable format — drop

        # ── Filter: null or zero value ────────────────────────
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if value == 0.0:
            continue   # zero = LLM placeholder, not real data

        # ── Step C: Conflict resolution ───────────────────────
        if canon_year not in best:
            best[canon_year] = value
        else:
            existing = best[canon_year]
            if existing == value:
                pass   # identical — keep silently
            else:
                # Different values for the same year — higher wins
                winner = max(existing, value)
                loser  = min(existing, value)
                best[canon_year] = winner
                errors.append(
                    f"[{filename}] Duplicate {array_name} for year {canon_year}: "
                    f"values {existing} vs {value} — keeping {winner} (higher)."
                )

    # Return sorted by year ascending
    return [
        {"year": yr, "value": val}
        for yr, val in sorted(best.items())
    ]


# ══════════════════════════════════════════════════════════════
# YEAR CANONICALIZATION
# ══════════════════════════════════════════════════════════════

def _canonicalize_year(raw: str) -> Optional[str]:
    """
    Normalize a raw year string to a 4-digit calendar year string.

    Handles all common formats found in Indian financial documents:

      "FY2023"          → "2023"
      "FY 2022"         → "2022"
      "2022-23"         → "2023"   (closing year of Indian fiscal year)
      "2022-2023"       → "2023"   (take later year)
      "2021-22"         → "2022"
      "March 2023"      → "2023"
      "Mar 2023"        → "2023"
      "31st March 2022" → "2022"
      "Q1 FY2023"       → "2023"
      "Q3 2024"         → "2024"
      "2023"            → "2023"   (already canonical)
      "23"              → None     (too ambiguous)
      "AY2023-24"       → None     (assessment year — not financial year)
      "null"            → None
      ""                → None

    Returns:
        4-digit year string (e.g. "2023") or None if unrecognizable.
    """
    if not raw:
        return None

    text = raw.strip().lower()

    if text in ("null", "none", "na", "n/a", "-", ""):
        return None

    # ── Assessment Year — explicitly rejected ─────────────────
    # "AY2023-24" / "assessment year" — these are tax assessment years,
    # NOT financial reporting years. The LLM prompt already rejects them
    # but canonicalization adds a second layer of defense.
    if re.match(r"^ay\s*20\d{2}", text):
        return None
    if "assessment year" in text:
        return None

    # ── FY2023 / FY 2022 ──────────────────────────────────────
    m = re.match(r"^fy\s*(20\d{2})$", text)
    if m:
        return m.group(1)

    # ── Q[1-4] FY2023 / Q3 2024 ──────────────────────────────
    m = re.match(r"^q[1-4]\s*(?:fy)?\s*(20\d{2})$", text)
    if m:
        return m.group(1)

    # ── Indian fiscal year ranges: "2022-23" or "2022-2023" ───
    # Take the CLOSING (later) year — in India FY2022-23 ends March 2023
    m = re.match(r"^(20\d{2})[-/](20)?(\d{2})$", text)
    if m:
        start_year = int(m.group(1))
        suffix     = m.group(3)
        # "2022-23" or "2022-2023" → closing year = 2023
        # group(3) is always 2 digits (regex guarantees \d{2})
        closing = (start_year // 100) * 100 + int(suffix)
        # Handle century boundary (e.g. "1999-00" → 2000)
        if closing < start_year:
            closing += 100
        return str(closing)

    # ── "March 2023" / "Mar 2023" ─────────────────────────────
    m = re.search(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
                  r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|"
                  r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(20\d{2})\b", text)
    if m:
        return m.group(2)

    # ── "31st March 2022" / "31 march 2022" ──────────────────
    m = re.search(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:\w+\s+)(20\d{2})\b", text)
    if m:
        return m.group(1)

    # ── Plain 4-digit year "2023" ─────────────────────────────
    m = re.match(r"^(20\d{2})$", text)
    if m:
        return m.group(1)

    # ── Year anywhere in the string as last resort ────────────
    # e.g. "period ending 2023", "year 2022"
    m = re.search(r"\b(20\d{2})\b", text)
    if m:
        return m.group(1)

    return None   # Could not canonicalize


# ══════════════════════════════════════════════════════════════
# UNCHANGED HELPERS (preserved exactly from original)
# ══════════════════════════════════════════════════════════════

# _CF_ANCHOR_PATTERNS loaded from config.yaml via CF_ANCHOR_PATTERNS (imported above).
_CF_ANCHOR_PATTERNS = CF_ANCHOR_PATTERNS


def _build_text_context(filepath: str, raw_text: str) -> str:
    """
    Build the text context sent to the cleaning LLM.

    Two-part strategy that works with plain PyMuPDF text (no ## headers):

    Part 1 — Cash Flow extraction from raw_text directly:
      Scans the FULL raw_text for any Cash Flow anchor phrase
      (e.g. "Cash Flow Statement", "Cash flows from operating activities").
      When found, extracts _CF_EXTRACT_WINDOW chars from that position.
      This works regardless of where in the document the CF section appears
      (page 1 or page 30) because it searches the entire raw_text.

    Part 2 — Fill remaining budget with general sections:
      Uses preprocess_document() sections for everything else
      (company identifiers, P&L, balance sheet figures).
      CF-only sections are skipped here to avoid duplication.

    Combined result fits within MAX_TEXT_CHARS.
    """
    # Budget allocation (always sums to CLEANING_MAX_TEXT_CHARS):
    #   HEADER_CHARS : cover page — company name, document title
    #   CF_CHARS     : cash flow section (if found)
    #   OTHER_CHARS  : remaining — balance sheet, P&L identifiers
    HEADER_CHARS = CLEANING_HEADER_CHARS
    CF_CHARS     = CLEANING_CF_CHARS
    OTHER_CHARS  = CLEANING_MAX_TEXT_CHARS - HEADER_CHARS - CF_CHARS

    # ── Part 1: Document header (always first) ────────────────
    # First 400 chars always contain the company name and document title.
    # This ensures company_name is available to the LLM regardless of
    # where the CF section appears — fixes mismatch filter null issue.
    header_chunk = raw_text[:HEADER_CHARS].strip()

    # ── Part 2: Find CF section anywhere in raw_text ─────────
    cf_chunk  = ""
    cf_start  = -1
    raw_lower = raw_text.lower()
    for anchor in _CF_ANCHOR_PATTERNS:
        idx = raw_lower.find(anchor)
        if idx != -1:
            cf_chunk = raw_text[idx: idx + CF_CHARS].strip()
            cf_start = idx
            break

    # ── Part 3: Fill remaining budget with other sections ─────
    other_text = ""
    try:
        parsed   = parse_document(filepath)
        sections = parsed.get("sections", [])
        for section in sections:
            if len(section.strip()) < 100:
                continue
            if cf_start != -1 and any(
                anchor in section.lower() for anchor in _CF_ANCHOR_PATTERNS
            ):
                continue   # skip CF sections — already covered in Part 2
            if len(other_text) + len(section) + 2 <= OTHER_CHARS:
                other_text += section + "\n\n"
            else:
                break
    except Exception:
        # Fallback: middle slice of raw_text (after header, excluding CF area)
        other_text = raw_text[HEADER_CHARS: HEADER_CHARS + OTHER_CHARS]

    # Assemble: header first (company name), then CF, then other
    parts = [header_chunk]
    if cf_chunk:
        parts.append(cf_chunk)
    if other_text.strip():
        parts.append(other_text.strip())

    combined = "\n\n".join(p for p in parts if p)
    return combined[:MAX_TEXT_CHARS] or raw_text[:MAX_TEXT_CHARS]


def _build_table_context(tables: list) -> str:
    """
    Serialize extracted table dicts into a readable text block for the LLM.

    Ordering: financial tables (is_financial_table=True from Node 4) are
    placed first so they occupy the limited character budget before any
    non-financial auxiliary tables.

    Row limits per table:
      Financial tables  → up to 30 rows (balance sheets, P&L, cash flow
                          can have 15-25 line items; we show them all)
      Non-financial     → up to 8 rows  (save budget for financial data)

    Total output is capped at MAX_TABLE_CHARS.
    """
    if not tables:
        return "No tables extracted."

    # Sort: financial tables first; within each group, preserve original order
    sorted_tables = sorted(
        tables,
        key=lambda t: 0 if t.get("financial_insights", {}).get("is_financial_table") else 1,
    )

    lines = []
    for i, table in enumerate(sorted_tables, 1):
        headers  = table.get("headers", [])
        rows     = table.get("rows", [])
        source   = table.get("sheet", table.get("source", "unknown"))
        num_rows = table.get("num_rows", len(rows))
        num_cols = table.get("num_cols", len(headers))
        is_fin   = table.get("financial_insights", {}).get("is_financial_table", False)

        lines.append(
            f"TABLE {i} [{source}] — {num_rows} rows × {num_cols} cols"
            + (" [FINANCIAL]" if is_fin else "")
        )

        if headers:
            lines.append("Headers: " + " | ".join(str(h) for h in headers))

        row_limit = 30 if is_fin else 8
        for j, row in enumerate(rows[:row_limit]):
            if isinstance(row, dict):
                vals = " | ".join(
                    f"{k}: {v}"
                    for k, v in row.items()
                    if v is not None and str(v).strip() != ""
                )
            else:
                vals = str(row)
            lines.append(f"  Row {j + 1}: {vals}")

        if len(rows) > row_limit:
            lines.append(f"  ... ({len(rows) - row_limit} more rows truncated)")

        lines.append("")   # Blank line between tables

    result = "\n".join(lines)
    return result[:MAX_TABLE_CHARS]


def _safe_parse_json(raw: str) -> dict:
    """
    Safely parse JSON from LLM response.
    Handles common Llama 3.1 8B output issues:
      - Wrapped in ```json ... ``` fences
      - Leading/trailing whitespace
    """
    cleaned = raw.strip()
    cleaned = cleaned.lstrip("```json").lstrip("```").rstrip("```").strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end   = cleaned.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Could not parse JSON from LLM response: {raw[:200]}")


def _log_extracted_fields(data: dict):
    """Print a brief summary of what was extracted."""
    fields = []
    if data.get("company_name"):
        fields.append(f"company={data['company_name']}")
    if data.get("years_found"):
        fields.append(f"years={data['years_found']}")
    if data.get("net_sales_figures"):
        fields.append(f"net_sales_entries={len(data['net_sales_figures'])}")
    if data.get("fixed_assets_figures"):
        fields.append(f"fixed_assets_entries={len(data['fixed_assets_figures'])}")
    if fields:
        print(f"      → {' | '.join(fields)}")