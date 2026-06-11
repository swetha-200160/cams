# nodes/table_detection.py
# ──────────────────────────────────────────────────────────────
# NODE 4 — Table Detection & Extraction
#
# Retrieves structured tables from each document AND enriches them
# with financial intelligence — field identification, year detection,
# value extraction, and table type classification.
#
# TABLE SOURCES (unchanged from original):
#   PDF/DOCX/DOC → _parse_markdown_tables() in docling_reader.py
#                  (Docling TableStructureRecognizer)
#   Excel         → _parse_excel() in docling_reader.py (pandas)
#
# PARSE CACHE: parse_document() is cached. This node retrieves the
#   SAME result as Nodes 2 and 3. No additional file I/O.
#
# ── NEW: FINANCIAL ENRICHMENT ──────────────────────────────────
# After collecting raw tables, each table is passed through
# _enrich_table() which adds a `financial_insights` field:
#
#   financial_type      : "Balance Sheet" | "Income Statement" |
#                         "Cash Flow Statement" | "Bank Statement" |
#                         "GST Return" | "Tax Summary" | "Unknown"
#   is_financial_table  : bool — True if any financial field matched
#   financial_fields_found : List[str] — FINANCIAL_FIELDS keys matched
#   table_layout        : "row_based" | "col_based"
#   years_detected      : List[str] — fiscal year labels found
#   extracted_values    : {
#       "revenue":      {"FY2023": 7400000, "FY2022": 6200000},
#       "total_assets": {"FY2023": 15000000},
#       ...
#   }
#
# TABLE LAYOUTS (both handled):
#   row_based : First column = line items, remaining = year columns
#               (most common in Indian financial statements)
#               | Particulars  | FY2023     | FY2022     |
#               | Revenue      | 7,400,000  | 6,200,000  |
#
#   col_based : First column = year, remaining = financial fields
#               | Year   | Revenue   | Net Profit | Total Assets |
#               | FY2023 | 7,400,000 | 620,000    | 15,000,000   |
#
# EXISTING TABLE DICT FIELDS (all preserved):
#   headers  : List[str]
#   rows     : List[Dict]
#   num_rows : int
#   num_cols : int
#   source   : str  — "markdown_table" | "excel_sheet"
#   sheet    : str  — sheet name (Excel only)
# ──────────────────────────────────────────────────────────────

import re
from typing import Optional

from config.settings import (
    MIN_TABLE_ROWS,
    FINANCIAL_FIELDS,
    TABLE_TYPE_MIN_HITS,
    TABLE_TYPE_SIGNATURES,
    NULL_VALUE_STRINGS,
)
from tools.docling_reader import parse_document
from state.agent_state import AgentState


# ── FINANCIAL_FIELDS, TABLE_TYPE_MIN_HITS, TABLE_TYPE_SIGNATURES ──
# All loaded from config/config.yaml via config.settings (imported above).
# Edit config.yaml to add/remove fields or keywords.

# ── Year patterns ─────────────────────────────────────────────
# Regex patterns — kept in code because they are programmatic expressions,
# not plain data. To add a new year format, add a pattern here.
YEAR_PATTERNS = [
    r"\bfy\s*20\d{2}\b",          # FY2022, FY 2022
    r"\bay\s*20\d{2}\b",          # AY2023, AY 2022-23
    r"\b20\d{2}-\d{2,4}\b",       # 2022-23, 2022-2023
    r"\b20\d{2}/\d{2,4}\b",       # 2022/23
    r"\bq[1-4]\s*20\d{2}\b",      # Q1 2023, Q3FY2024
    r"\bmarch\s+20\d{2}\b",       # March 2023
    r"\bmar\s+20\d{2}\b",         # Mar 2023
    r"\b31st?\s+march\s+20\d{2}\b", # 31st March 2023
    r"\b20\d{2}\b",               # plain year: 2022 (least specific, last)
]


# ══════════════════════════════════════════════════════════════
# NODE FUNCTION
# ══════════════════════════════════════════════════════════════

def table_detection_node(state: AgentState) -> AgentState:
    print("\n" + "─" * 55)
    print("📊  NODE 4 — Table Detection & Extraction")
    print("─" * 55)

    errors           = list(state.get("errors", []))
    extracted_tables = {}

    for doc in state["classified_documents"]:
        filename = doc["filename"]
        filepath = doc["filepath"]
        doc_type = doc.get("doc_type", "Unknown")

        try:
            # ── Retrieve cached parse result ──────────────────
            # parse_document() was already called in Nodes 2 and 3.
            # This returns the cached result — no re-parsing.
            parsed     = parse_document(filepath)
            all_tables = parsed.get("tables", [])

            # ── Filter: remove tables that are too small ──────
            valid_tables = []
            skipped      = 0
            for table in all_tables:
                if table.get("num_rows", 0) >= MIN_TABLE_ROWS:
                    valid_tables.append(table)
                else:
                    skipped += 1

            # ── Header Normalisation ───────────────────────────────
            # Two-pass header repair applied to every table before enrichment:
            #
            # Pass 1 — First-row-as-header promotion:
            #   When ALL column headers are generic placeholders (col0/Col0/…),
            #   inspect the first data row.  If it contains year-like values
            #   or non-numeric text in every cell, treat it as the header row
            #   and shift remaining rows down.  This catches tables where
            #   MinerU/PyMuPDF failed to separate the header row from data.
            #
            # Pass 2 — Year-sequence propagation:
            #   Track the last table whose headers contained recognisable year
            #   labels.  Apply those labels to the value columns of any
            #   subsequent table whose own headers are generic.  Fixes cash
            #   flow tables that share the same year columns as the preceding
            #   balance sheet but whose own header row was dropped during
            #   parsing.
            #
            # Both passes are purely structural — no financial field names are
            # hardcoded; only year-pattern detection is used.

            last_year_sequence: list[str] = []

            for table in valid_tables:
                headers = table.get("headers", [])
                rows    = table.get("rows", [])

                # ── Pass 1: Promote first data row to headers ──────────
                # Triggered when every column header is a generic placeholder.
                all_generic = bool(headers) and all(
                    re.match(r"^col\d+$", str(h), re.IGNORECASE) or not str(h).strip()
                    for h in headers
                )
                if all_generic and rows:
                    first_row     = rows[0]
                    first_row_vals = [str(first_row.get(h, "")).strip() for h in headers]

                    # Promote if the first row looks like a header:
                    # at least one value is a year label OR at least half of
                    # the values are non-numeric (i.e. label text, not data).
                    year_hits       = sum(1 for v in first_row_vals[1:] if _is_year_label(v))
                    non_numeric     = sum(
                        1 for v in first_row_vals
                        if v and not re.match(r"^[-\d,.()\s]+$", v)
                    )
                    should_promote  = year_hits >= 1 or non_numeric >= max(1, len(headers) // 2)

                    if should_promote:
                        new_headers = [
                            v if v else f"col_{k}"
                            for k, v in enumerate(first_row_vals)
                        ]
                        new_rows = []
                        for row in rows[1:]:   # skip the promoted header row
                            new_row = {
                                new_headers[k]: row.get(h, "")
                                for k, h in enumerate(headers)
                            }
                            new_rows.append(new_row)

                        table["headers"] = new_headers
                        table["rows"]    = new_rows
                        headers          = new_headers   # update for Pass 2
                        print(
                            f"      🔧 Promoted first data row to headers "
                            f"on page {table.get('page', '?')}: {new_headers}"
                        )

                # ── Pass 2: Year-sequence propagation ─────────────────
                current_years = [str(h) for h in headers[1:] if _is_year_label(str(h))]

                if current_years:
                    last_year_sequence = current_years
                elif not current_years and last_year_sequence and len(headers) > 1:
                    # Apply the last known year sequence to the value columns
                    new_headers = [headers[0]]
                    for idx in range(1, len(headers)):
                        if idx - 1 < len(last_year_sequence):
                            new_headers.append(last_year_sequence[idx - 1])
                        else:
                            new_headers.append(headers[idx])

                    # Rewrite rows to use the new header names
                    new_rows = []
                    for row in table.get("rows", []):
                        new_row = {
                            new_h: row.get(old_h, "")
                            for old_h, new_h in zip(headers, new_headers)
                        }
                        new_rows.append(new_row)

                    table["headers"] = new_headers
                    table["rows"]    = new_rows
                    print(
                        f"      🔄 Propagated years {last_year_sequence} "
                        f"to table on page {table.get('page', '?')}"
                    )

            # ── Enrich: add financial_insights to each table ──
            for table in valid_tables:
                table["financial_insights"] = _enrich_table(table, doc_type)

            extracted_tables[filename] = valid_tables

            # ── Log results ───────────────────────────────────
            print(f"   ✅ {filename}  →  {len(valid_tables)} tables found", end="")
            if skipped:
                print(f"  ({skipped} too small, discarded)", end="")
            print()

            for i, table in enumerate(valid_tables):
                src      = table.get("sheet", table.get("source", "?"))
                insights = table["financial_insights"]
                fin_tag  = (
                    f"  💰 {insights['financial_type']}  "
                    f"[{', '.join(insights['financial_fields_found'][:3])}]"
                    if insights["is_financial_table"]
                    else "  (non-financial)"
                )
                print(
                    f"      Table {i + 1}: "
                    f"{table['num_rows']} rows × {table['num_cols']} cols  "
                    f"[{src}]{fin_tag}"
                )

        except Exception as e:
            err = f"Table detection failed for {filename}: {e}"
            print(f"   ❌ {err}")
            errors.append(err)
            extracted_tables[filename] = []

    total_tables    = sum(len(t) for t in extracted_tables.values())
    financial_count = sum(
        1
        for tables in extracted_tables.values()
        for t in tables
        if t.get("financial_insights", {}).get("is_financial_table")
    )
    print(f"\n   📊 Total tables extracted  : {total_tables}")
    print(f"   💰 Financial tables found  : {financial_count}")

    return {
        **state,
        "extracted_tables": extracted_tables,
        "errors": errors,
        "current_step": "table_detection",
    }


# ══════════════════════════════════════════════════════════════
# ENRICHMENT ENGINE
# ══════════════════════════════════════════════════════════════

def _enrich_table(table: dict, doc_type: str) -> dict:
    """
    Analyse a table dict and return a financial_insights dict.

    Steps:
      1. Detect table layout (row_based or col_based)
      2. Detect year columns
      3. Classify table type (Balance Sheet, Income Statement, etc.)
      4. Scan for financial fields and extract values

    Args:
        table    : Raw table dict with headers + rows
        doc_type : Document type from Node 2 (provides context hint)

    Returns:
        financial_insights dict (always returned, even if table is non-financial)
    """
    headers = table.get("headers", [])
    rows    = table.get("rows", [])

    if not headers or not rows:
        return _empty_insights()

    # ── Step 1: Detect layout ─────────────────────────────────
    layout = _detect_layout(headers, rows)

    # ── Step 2: Detect year columns ───────────────────────────
    years = _detect_year_columns(headers, rows, layout)

    # ── Step 3: Classify table type ──────────────────────────
    # Build a combined text from headers + first-column values
    # for type detection scoring
    first_col_values = [
        str(row.get(headers[0], ""))
        for row in rows
        if headers
    ] if headers else []

    combined_text = " ".join(headers + first_col_values).lower()
    table_type    = _classify_table_type(combined_text, doc_type)

    # ── Step 4: Extract financial field values ────────────────
    extracted_values, fields_found = _extract_financial_values(
        headers, rows, layout, years
    )

    return {
        "financial_type":         table_type,
        "is_financial_table":     len(fields_found) > 0,
        "financial_fields_found": fields_found,
        "table_layout":           layout,
        "years_detected":         years,
        "extracted_values":       extracted_values,
    }


def _empty_insights() -> dict:
    """Return empty insights structure for tables with no data."""
    return {
        "financial_type":         "Unknown",
        "is_financial_table":     False,
        "financial_fields_found": [],
        "table_layout":           "unknown",
        "years_detected":         [],
        "extracted_values":       {},
    }


# ══════════════════════════════════════════════════════════════
# LAYOUT DETECTION
# ══════════════════════════════════════════════════════════════

def _detect_layout(headers: list, rows: list) -> str:
    """
    Determine whether a table uses row-based or column-based layout.

    Row-based  : First column = line item labels (e.g. "Revenue"),
                 Remaining columns = year values (FY2023, FY2022)
                 Most common in Indian financial statements.

    Col-based  : First column = year labels (FY2023, FY2022),
                 Remaining columns = financial fields (Revenue, Profit)

    Detection:
      - If any non-first header looks like a year → row_based
      - If any non-first header matches a financial field → col_based
      - Check first column values for year patterns → col_based
      - Default → row_based (more common)
    """
    if not headers:
        return "row_based"

    non_first_headers = [str(h).lower() for h in headers[1:]]

    # Check if non-first headers look like years → row_based
    for h in non_first_headers:
        if _is_year_label(h):
            return "row_based"

    # Check if non-first headers match financial field names → col_based
    for h in non_first_headers:
        if _match_financial_field(h) is not None:
            return "col_based"

    # Check first column values for year patterns → col_based
    if rows and headers:
        first_col_key = headers[0]
        first_col_vals = [str(row.get(first_col_key, "")).lower() for row in rows[:4]]
        year_hits = sum(1 for v in first_col_vals if _is_year_label(v))
        if year_hits >= 2:
            return "col_based"

    return "row_based"   # default — most common in Indian financial docs


# ══════════════════════════════════════════════════════════════
# YEAR COLUMN DETECTION
# ══════════════════════════════════════════════════════════════

def _detect_year_columns(headers: list, rows: list, layout: str) -> list:
    """
    Return a list of year labels found in the table.

    For row_based : year labels are in the headers (columns 2 onward)
    For col_based : year labels are in the first column's row values
    """
    years = []

    if layout == "row_based":
        # Skip first header (it's the label column)
        for h in headers[1:]:
            if _is_year_label(str(h)):
                years.append(str(h).strip())

    elif layout == "col_based":
        if headers and rows:
            first_col_key = headers[0]
            for row in rows:
                val = str(row.get(first_col_key, "")).strip()
                if _is_year_label(val):
                    years.append(val)

    return years


def _is_year_label(text: str) -> bool:
    """Return True if the text looks like a fiscal year label."""
    text_lower = text.lower().strip()
    for pattern in YEAR_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


# ══════════════════════════════════════════════════════════════
# TABLE TYPE CLASSIFICATION
# ══════════════════════════════════════════════════════════════

def _classify_table_type(combined_text: str, doc_type: str) -> str:
    """
    Score TABLE_TYPE_SIGNATURES against combined_text (headers +
    first-column values, lowercased).

    Also uses doc_type from Node 2 as a tiebreaker:
    If two types score equally, the one matching doc_type wins.

    Returns the best matching table type, or "Unknown".
    """
    scores = {}
    for ttype, keywords in TABLE_TYPE_SIGNATURES.items():
        hits = sum(1 for kw in keywords if kw in combined_text)
        if hits >= TABLE_TYPE_MIN_HITS:
            scores[ttype] = hits

    if not scores:
        return "Unknown"

    # Sort by score descending
    sorted_types = sorted(scores.items(), key=lambda x: -x[1])

    # If doc_type hint aligns with a scoring type, boost it
    doc_type_lower = doc_type.lower()
    for ttype, score in sorted_types:
        if ttype.lower() in doc_type_lower or doc_type_lower in ttype.lower():
            return ttype

    return sorted_types[0][0]


# ══════════════════════════════════════════════════════════════
# FINANCIAL VALUE EXTRACTION
# ══════════════════════════════════════════════════════════════

def _extract_financial_values(
    headers: list, rows: list, layout: str, years: list
) -> tuple:
    """
    Extract financial field values from a table.

    Returns:
        (extracted_values, fields_found)
        extracted_values : {
            "revenue": {"FY2023": 7400000, "FY2022": 6200000},
            "total_assets": {"FY2023": 15000000},
        }
        fields_found : List[str] of FINANCIAL_FIELDS keys that were matched
    """
    extracted_values = {}
    fields_found     = []

    if not headers or not rows:
        return extracted_values, fields_found

    if layout == "row_based":
        extracted_values, fields_found = _extract_row_based(headers, rows)
    elif layout == "col_based":
        extracted_values, fields_found = _extract_col_based(headers, rows)

    return extracted_values, fields_found


def _extract_row_based(headers: list, rows: list) -> tuple:
    """
    Extract from row-based layout.

    Structure:
        | Particulars  | FY2023    | FY2022    |
        | Revenue      | 7,400,000 | 6,200,000 |
        | Total Assets | 15,00,000 | 12,00,000 |

    - First column = line item label
    - Remaining columns = year values (use as-is as year keys)
    """
    extracted_values = {}
    fields_found     = []

    if len(headers) < 2:
        return extracted_values, fields_found

    label_col   = headers[0]        # first column = item labels
    value_cols  = headers[1:]       # remaining columns = years or periods

    for row in rows:
        label = str(row.get(label_col, "")).strip()
        if not label:
            continue

        field_key = _match_financial_field(label)
        if field_key is None:
            continue

        # Extract value for each column (year)
        year_values = {}
        for col in value_cols:
            raw_val = str(row.get(col, "")).strip()
            numeric = _parse_numeric(raw_val)
            if numeric is not None:
                year_values[col] = numeric

        if year_values:
            if field_key not in extracted_values:
                extracted_values[field_key] = {}
                fields_found.append(field_key)
            extracted_values[field_key].update(year_values)

    return extracted_values, fields_found


def _extract_col_based(headers: list, rows: list) -> tuple:
    """
    Extract from col-based layout.

    Structure:
        | Year   | Revenue   | Net Profit | Total Assets |
        | FY2023 | 7,400,000 | 620,000    | 15,000,000   |
        | FY2022 | 6,200,000 | 550,000    | 12,000,000   |

    - First column = year labels
    - Remaining columns = financial field names
    """
    extracted_values = {}
    fields_found     = []

    year_col     = headers[0]     # first column = year labels
    field_cols   = headers[1:]    # remaining = financial field names

    # Pre-match field column names to FINANCIAL_FIELDS keys
    col_field_map = {}
    for col in field_cols:
        field_key = _match_financial_field(str(col))
        if field_key is not None:
            col_field_map[col] = field_key

    if not col_field_map:
        return extracted_values, fields_found

    for row in rows:
        year_label = str(row.get(year_col, "")).strip()
        if not year_label:
            continue

        for col, field_key in col_field_map.items():
            raw_val = str(row.get(col, "")).strip()
            numeric = _parse_numeric(raw_val)
            if numeric is not None:
                if field_key not in extracted_values:
                    extracted_values[field_key] = {}
                    if field_key not in fields_found:
                        fields_found.append(field_key)
                extracted_values[field_key][year_label] = numeric

    return extracted_values, fields_found


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def _match_financial_field(text: str) -> Optional[str]:
    """
    Match a text string against FINANCIAL_FIELDS synonyms.

    Uses case-insensitive containment: checks if a known synonym
    appears IN the cell text (synonym in text_lower).

    Intentionally does NOT check the reverse (text in synonym) to
    prevent false positives — e.g. "depreciation" would incorrectly
    match the EBITDA synonym "earnings before interest tax depreciation"
    if the reverse check were allowed.

    Returns the FINANCIAL_FIELDS key if matched, else None.
    """
    text_lower = text.lower().strip()
    if not text_lower:
        return None

    for field_key, synonyms in FINANCIAL_FIELDS.items():
        for synonym in synonyms:
            # Check: known synonym appears in cell text
            # e.g. "net profit" in "net profit after tax" → True
            if synonym in text_lower:
                return field_key

    return None


def _parse_numeric(raw: str) -> Optional[float]:
    """
    Convert a raw cell value string to a float.

    Handles all common Indian financial document formats:
      "1,50,000"          → 150000.0  (Indian comma grouping)
      "(50,000)"          → -50000.0  (brackets = negative)
      "₹ 50,000"          → 50000.0   (Rupee symbol)
      "Rs. 1,23,456"      → 123456.0  (Rs prefix)
      "50.5 crores"       → 50.5      (unit suffix — kept as-is)
      "1,500 lakhs"       → 1500.0    (unit suffix — kept as-is)
      "-50000"            → -50000.0  (plain negative)
      "N/A", "-", ""      → None      (missing / not applicable)

    NOTE: "crores" and "lakhs" units are preserved as their stated value
    because the full conversion (×10^7 / ×10^5) needs to happen in
    Node 5 (Data Cleaning) once all document units are normalised.
    """
    if not raw or raw.strip() in NULL_VALUE_STRINGS:
        return None

    text = raw.strip()

    # ── Check for bracket notation (negative) ─────────────────
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()

    # ── Strip currency symbols and unit words ─────────────────
    text = re.sub(r"[₹$£€]", "", text)           # currency symbols
    # Remove "Rs." / "Rs" / "INR" — use \brs\.?\s* (not \brs\.?\b)
    # because after the dot there's a space, not a word char,
    # so \b would fail and leave a leading "." that corrupts the number.
    text = re.sub(r"\brs\.?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\binr\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bcrores?\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\blakhs?\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthousands?\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmillions?\b", "", text, flags=re.IGNORECASE)

    # ── Remove commas (both Indian and international grouping) ─
    text = text.replace(",", "").strip()

    # ── Strip any remaining non-numeric chars (except . and -) ─
    text = re.sub(r"[^\d.\-]", "", text).strip()

    if not text:
        return None

    try:
        value = float(text)
        return -value if negative else value
    except ValueError:
        return None