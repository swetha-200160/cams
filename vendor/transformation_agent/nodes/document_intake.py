# nodes/document_intake.py
# ──────────────────────────────────────────────────────────────
# NODE 1 — Document Intake
#
# RESPONSIBILITIES (per CAMS requirement):
#   1. Scan input_folder and collect all valid documents
#   2. Pre-classify every document into one of 5 CAMS categories
#      using filename keyword matching (rule-based, no LLM)
#   3. Assign a best-guess doc_type within each category
#   4. Build the document_repository with full classification fields
#   5. Hand off to Node 2 (LLM) which refines/confirms the labels
#
# WHY PRE-CLASSIFY HERE (not just leave it to Node 2)?
#   - Borrowers upload files with descriptive names:
#       "HDFC_Bank_Statement_FY24.pdf", "GSTR3B_Q3_2023.xlsx"
#   - Filename alone classifies ~80% of documents correctly
#   - Giving Node 2 a pre-label reduces LLM hallucination rate
#   - Node 2 confirms or corrects rather than guessing cold
#   - classification_source field tells Node 2 how confident to be
#
# CAMS DOCUMENT CATEGORIES (5):
#   Financial Statement  → Balance Sheet, P&L / Income Statement,
#                          Cash Flow Statement, General Financial Statement
#   Bank Statement       → Savings / Current / OD / Credit Card statements
#   GST Return           → GSTR-1, GSTR-3B, GSTR-9, GSTR-9C, etc.
#   Income Tax Return    → ITR-1 to ITR-7, Form 16, Form 26AS / AIS
#   ROC Filing           → MGT-7, AOC-4, DIR-12, COI, MOA, AOA, etc.
#
# CLASSIFICATION OUTPUT FIELDS (per document dict):
#   category              → broad CAMS category (one of 5 above)
#   doc_type              → specific document type within category
#   classification_source → "rule_based" | "unclassified"
#                           Node 2 uses this to decide how hard to look
# ──────────────────────────────────────────────────────────────

import os
import re
from typing import Tuple
from state.agent_state import AgentState
from config.settings import SUPPORTED_EXTENSIONS, CATEGORY_RULES

# ── Temp-artifact detection ────────────────────────────────────────────────
# DocumentLoader._extract_pdf() creates temp PNGs named
#   "{original_path}_page{N}_ocr_tmp.png" for scanned pages.
# If the process is interrupted, these persist in the input folder.
# They must never be treated as real input documents.
_TEMP_ARTIFACT_RE = re.compile(r'_page\d+_.*tmp', re.IGNORECASE)


# ── CATEGORY_RULES loaded from config.yaml ────────────────────
# Structure: [{label, subtypes: [(keywords_list, doc_type_str)]}]
# Edit config/config.yaml → category_rules to add/change document types.


# CATEGORY_RULES is imported from config.settings (loaded from config.yaml).
# To add a new document category or keyword pattern, edit config/config.yaml.


# ══════════════════════════════════════════════════════════════
# CLASSIFICATION ENGINE
# ══════════════════════════════════════════════════════════════

def _normalise(filename: str) -> str:
    """
    Normalise a filename for keyword matching.

    Steps:
      1. Strip file extension
      2. Convert to lowercase
      3. Replace underscores, hyphens, dots with spaces
      4. Collapse multiple spaces
      5. Strip whitespace

    Examples:
      "HDFC_BankStatement_Q3-FY2024.pdf" → "hdfc bankstatement q3 fy2024"
      "GSTR-3B_July_2023.xlsx"           → "gstr 3b july 2023"
      "Balance_Sheet_FY22-23.pdf"        → "balance sheet fy22 23"
    """
    name = os.path.splitext(filename)[0]
    name = name.lower()
    name = re.sub(r"[_\-\.]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _is_temp_artifact(filename: str) -> bool:
    """
    Return True if the filename is a temporary extraction artifact.

    Matches patterns like 'somefile.pdf_page4_ocr_tmp.png' or any
    variant ending with '_page{N}_*tmp*'. These are created by
    DocumentLoader._extract_pdf() for scanned pages and should be
    deleted automatically, but may persist if a run is interrupted.
    """
    return bool(_TEMP_ARTIFACT_RE.search(filename))


def _classify_by_filename(filename: str) -> Tuple[str, str, str]:
    """
    Rule-based filename keyword classifier.

    Iterates CATEGORY_RULES in definition order.
    Within each category, iterates subtypes from most specific
    to least specific. Returns on first keyword match.

    Returns:
        tuple of (category, doc_type, classification_source)
        - category              : broad CAMS category label
        - doc_type              : specific document type label
        - classification_source : "rule_based" if matched,
                                  "unclassified" if no keyword found
    """
    normalised = _normalise(filename)

    for rule in CATEGORY_RULES:
        for keywords, doc_type in rule["subtypes"]:
            for kw in keywords:
                if kw in normalised:
                    return rule["label"], doc_type, "rule_based"

    return "Unclassified", "Unknown", "unclassified"


# ══════════════════════════════════════════════════════════════
# NODE FUNCTION
# ══════════════════════════════════════════════════════════════

def document_intake_node(state: AgentState) -> AgentState:
    print("\n" + "─" * 55)
    print("📥  NODE 1 — Document Intake")
    print("─" * 55)

    folder = state["input_folder"]
    errors = list(state.get("errors", []))
    repository = []

    # ── Validate folder exists ────────────────────────────────
    if not os.path.exists(folder):
        msg = f"Input folder not found: '{folder}'"
        print(f"   ❌ {msg}")
        errors.append(msg)
        return {
            **state,
            "document_repository": [],
            "errors": errors,
            "current_step": "document_intake",
        }

    if not os.path.isdir(folder):
        msg = f"Input path is not a folder: '{folder}'"
        print(f"   ❌ {msg}")
        errors.append(msg)
        return {
            **state,
            "document_repository": [],
            "errors": errors,
            "current_step": "document_intake",
        }

    # ── Scan and classify ─────────────────────────────────────
    all_files = os.listdir(folder)
    if not all_files:
        print("   ⚠️  Input folder is empty — no documents to process.")

    category_counts = {}   # { category_label: count } for summary

    for filename in sorted(all_files):
        # Skip hidden files (.DS_Store, .gitkeep, etc.)
        if filename.startswith("."):
            continue

        # Skip temp extraction artifacts left over from interrupted runs
        if _is_temp_artifact(filename):
            print(f"   ⏭  Skipping temp artifact: {filename}")
            continue

        filepath = os.path.join(folder, filename)

        # Skip sub-directories — only flat file layout supported
        if os.path.isdir(filepath):
            print(f"   ⏭  Skipping sub-directory: {filename}")
            continue

        ext = os.path.splitext(filename)[1].lower()

        # ── Reject unsupported file types ─────────────────────
        if ext not in SUPPORTED_EXTENSIONS:
            msg = f"Unsupported file type skipped: {filename}  ({ext})"
            print(f"   ⏭  {msg}")
            errors.append(msg)
            continue

        # ── Classify document by filename keywords ─────────────
        category, doc_type, source = _classify_by_filename(filename)

        # Track per-category count for summary
        category_counts[category] = category_counts.get(category, 0) + 1

        # ── Build repository entry ────────────────────────────
        repository.append({
            "filename":              filename,
            "filepath":              os.path.abspath(filepath),
            "extension":             ext,

            # ── Classification fields ──────────────────────────
            # category: broad CAMS bucket (Financial Statement,
            #   Bank Statement, GST Return, Income Tax Return, ROC Filing)
            "category":              category,

            # doc_type: specific document type within the category.
            # Pre-filled here by keyword matching.
            # Node 2 (LLM) will confirm or correct this value.
            "doc_type":              doc_type,

            # classification_source tells Node 2 how confident to be:
            #   "rule_based"   → keyword matched → Node 2 verifies
            #   "unclassified" → no match → Node 2 must classify from content
            "classification_source": source,

            # Filled by Node 3 (ocr_extraction_node)
            "metadata":              {},
        })

        # ── Console log ───────────────────────────────────────
        if source == "rule_based":
            print(
                f"   {_category_icon(category)}  {filename}\n"
                f"      [{ext}]  →  {category}  /  {doc_type}"
            )
        else:
            print(
                f"   ❓  {filename}\n"
                f"      [{ext}]  →  Unclassified — Node 2 will classify from content"
            )

    # ── Category breakdown summary ────────────────────────────
    print(f"\n   {'─' * 48}")
    print(f"   📦 Total documents loaded : {len(repository)}")
    print(f"   📋 Category breakdown     :")
    for cat in [r["label"] for r in CATEGORY_RULES] + ["Unclassified"]:
        count = category_counts.get(cat, 0)
        if count:
            print(f"      {_category_icon(cat)}  {cat:<28} : {count} file(s)")

    unclassified = category_counts.get("Unclassified", 0)
    if unclassified:
        print(
            f"\n   ⚠️  {unclassified} file(s) could not be classified from filename alone.\n"
            f"      Node 2 (LLM) will classify these from document content."
        )
    else:
        print(
            f"\n   ✅ All documents pre-classified from filename.\n"
            f"      Node 2 (LLM) will verify each label."
        )

    return {
        **state,
        "document_repository": repository,
        "errors": errors,
        "current_step": "document_intake",
    }


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def _category_icon(category: str) -> str:
    """Terminal emoji icon per CAMS category."""
    return {
        "Financial Statement": "📊",
        "Bank Statement":      "🏦",
        "GST Return":          "🧾",
        "Income Tax Return":   "📑",
        "ROC Filing":          "🏛️",
        "Unclassified":        "❓",
    }.get(category, "📄")