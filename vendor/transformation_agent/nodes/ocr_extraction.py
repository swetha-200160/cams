# nodes/ocr_extraction.py
# ──────────────────────────────────────────────────────────────
# NODE 3 — OCR & Text Extraction
#
# Extracts full text, structured tables, and text sections from every
# classified document using parse_document() (via docling_reader.py).
#
# Document routing inside DocumentLoader:
#   PDF (native)   → PyMuPDF page.get_text()       → plain text
#   PDF (scanned)  → PyMuPDF pixmap → EasyOCR       → plain text
#   .docx          → python-docx                    → plain text
#   .doc           → Apache Tika                    → plain text
#   .txt           → direct file read               → plain text
#   image          → EasyOCR via OcrExtractor       → plain text
#   .xlsx / .xls   → pandas (all sheets)            → text + tables
#
# Table extraction:
#   PDF (native pages)  → PyMuPDF find_tables() (vector geometry)
#   PDF (scanned pages) → OCR line heuristic in docling_reader.py
#   Excel               → pandas sheet-level tables
#
# State keys produced by this node:
#   extracted_texts    : dict[filename → full text string]
#   extracted_tables   : dict[filename → List[table dict]]
#   extracted_sections : dict[filename → List[section string]]
#
# IMPORTANT: parse_document() is cached in docling_reader.py.
# Node 2 already triggered the parse for classification preview.
# This node retrieves the CACHED result — no re-parsing occurs.
#
# Changes from previous version:
#
#   Gap 1 — tables and sections were extracted by parse_document()
#     but then silently dropped. This node only stored extracted_texts.
#     Any downstream node needing tables had to call parse_document()
#     again (working only due to cache, and unintentionally).
#     Fix: tables and sections are now read from the parsed result and
#     stored in state as extracted_tables and extracted_sections.
#
#   Gap 3 (Node 3 side) — extraction errors from DocumentLoader
#     (OCR page failures, Tika failures) were only printed inside
#     the loader. They were invisible to state["errors"].
#     Fix: parse_document() now surfaces them in
#     metadata["extraction_errors"]. This node reads that list and
#     extends state["errors"] so all failures are trackable.
# ──────────────────────────────────────────────────────────────

from pathlib import Path
from tools.docling_reader import parse_document
from state.agent_state import AgentState


def ocr_extraction_node(state: AgentState) -> AgentState:
    print("\n" + "─" * 55)
    print("📝  NODE 3 — OCR & Text Extraction")
    print("─" * 55)

    errors             = list(state.get("errors", []))
    extracted_texts    = {}
    extracted_tables   = {}
    extracted_sections = {}

    for doc in state["classified_documents"]:
        filename = doc["filename"]
        filepath = doc["filepath"]

        try:
            # parse_document() returns cached result — no re-parse occurs
            # if Node 2 already parsed this file.
            parsed = parse_document(filepath)

            # ── Text ──────────────────────────────────────────
            text = parsed.get("text", "")
            extracted_texts[filename] = text

            # ── Tables ────────────────────────────────────────
            # Includes PyMuPDF native tables (PDF), OCR heuristic tables
            # (scanned PDF pages), and pandas tables (Excel).
            tables = parsed.get("tables", [])
            extracted_tables[filename] = tables

            # ── Sections ──────────────────────────────────────
            # Paragraph-aware 500-word chunks produced by
            # DocumentLoader.preprocess_document(). Downstream nodes
            # (e.g. Node 4 extraction, Node 5 validation) use these
            # to feed bounded text blocks to the LLM.
            sections = parsed.get("sections", [])
            extracted_sections[filename] = sections

            # ── Metadata back-fill ────────────────────────────
            doc["metadata"] = parsed.get("metadata", {})

            # ── Propagate loader-level extraction errors ───────
            # DocumentLoader collects per-page OCR failures and Tika
            # errors in metadata["extraction_errors"]. These are now
            # surfaced to state["errors"] so they are visible in the
            # final pipeline report instead of being silently dropped.
            loader_errors = doc["metadata"].get("extraction_errors", [])
            if loader_errors:
                errors.extend(loader_errors)

            # ── Console summary ───────────────────────────────
            char_count    = len(text)
            section_count = len(sections)
            table_count   = len(tables)

            print(
                f"   ✅ {filename}\n"
                f"      chars={char_count:,}  |  sections={section_count}"
                f"  |  tables={table_count}  |  type={doc['doc_type']}"
            )

            if char_count == 0:
                warn = (
                    f"Zero characters extracted from '{filename}' — "
                    "document may be corrupted, password-protected, or unreadable."
                )
                print(f"   ⚠️  {warn}")
                errors.append(warn)

        except Exception as e:
            err = f"Text extraction failed for '{filename}': {e}"
            print(f"   ❌ {err}")
            errors.append(err)
            extracted_texts[filename]    = ""
            extracted_tables[filename]   = []
            extracted_sections[filename] = []

    # ── Run summary ───────────────────────────────────────────
    total_chars   = sum(len(t) for t in extracted_texts.values())
    total_tables  = sum(len(t) for t in extracted_tables.values())

    print(f"\n   📊 Total characters extracted : {total_chars:,}")
    print(f"   📊 Total tables extracted     : {total_tables}")

    return {
        **state,
        "extracted_texts":    extracted_texts,
        "extracted_tables":   extracted_tables,
        "extracted_sections": extracted_sections,
        "errors":             errors,
        "current_step":       "ocr_extraction",
    }