# tools/docling_reader.py
# ──────────────────────────────────────────────────────────────
# Unified document parsing interface used by all pipeline nodes.
# Single entry point: parse_document(filepath) → dict.
#
# PDF / images → MinerU pipeline backend
#   - Layout analysis + reading-order detection
#   - Table recognition (HTML output → pandas DataFrame)
#   - Built-in OCR for scanned content
#   - Pages processed in batches of PDF_BATCH_SIZE to prevent OOM
#   - CPU-only; no CUDA required
#
# .docx  → python-docx (text + tables)
# .doc   → Apache Tika via DocumentLoader (MinerU doesn't support .doc)
# .txt   → direct file read via DocumentLoader
# Excel  → pandas (all sheets)
#
# Results are cached in _parse_cache for the duration of one run.
# clear_cache() is called by main.py at pipeline start.
# ──────────────────────────────────────────────────────────────

import gc
import io
import logging
import os
import re
import shutil
import tempfile
import pandas as pd
from pathlib import Path
from tools.document_loader import DocumentLoader

logger = logging.getLogger(__name__)


# ── Supported extension sets ──────────────────────────────────
_MINERU_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
_DOCX_EXTS   = {".docx"}

# ── In-memory parse cache ─────────────────────────────────────
# Key   : absolute filepath string
# Value : parsed result dict { text, sections, tables, metadata }
_parse_cache: dict = {}

# ── PDF batch size ────────────────────────────────────────────
# Number of pages passed to MinerU per call.
# Smaller = less peak RAM; larger = fewer model-reload cycles.
PDF_BATCH_SIZE = 50


# ══════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════

def parse_document(filepath: str) -> dict:
    """
    Parse any supported document and return a unified result dict.

    Format routing:
        .pdf    → MinerU (layout + OCR + table recognition)
        images  → MinerU (built-in OCR pipeline)
        .docx   → python-docx (paragraphs + tables)
        .xlsx   → pandas (all sheets)
        .xls    → pandas (all sheets)
        .doc    → Apache Tika via DocumentLoader
        .txt    → plain read via DocumentLoader

    Returns:
        {
            "text"     : str        — full extracted text (markdown for MinerU)
            "sections" : List[str]  — paragraph/header-aware chunks
            "tables"   : List[dict] — structured tables
            "metadata" : dict       — filepath, extension, counts,
                                      extraction_errors
        }

    Results are cached in _parse_cache by filepath string.
    Subsequent calls with the same filepath return from cache immediately.
    """
    if filepath in _parse_cache:
        logger.debug("Cache hit: %s", Path(filepath).name)
        return _parse_cache[filepath]

    ext = Path(filepath).suffix.lower()

    if ext in (".xlsx", ".xls"):
        result = _parse_excel(filepath)
    elif ext == ".csv":
        result = _parse_csv(filepath)
    elif ext in _MINERU_EXTS:
        result = _parse_with_mineru(filepath)
    elif ext in _DOCX_EXTS:
        result = _parse_docx(filepath)
    else:
        # .doc, .txt, or any other unsupported format
        result = _parse_with_document_loader(filepath)

    _parse_cache[filepath] = result
    return result


# ══════════════════════════════════════════════════════════════
# TABLE HELPER — parse MinerU content_list HTML tables
# ══════════════════════════════════════════════════════════════

def _mineru_extract_tables(
    content_list: list,
    source_name:  str,
    page_offset:  int = 0,
) -> list:
    """
    Convert MinerU content_list table items to the pipeline's table dict format.

    MinerU represents tables as HTML strings inside content_list entries
    with type == "table".  pandas.read_html() converts them to DataFrames.

    page_offset is added to each item's page_idx so absolute page numbers
    are preserved when tables come from a batch that started mid-document.
    """
    tables = []
    for item in content_list:
        if item.get("type") != "table":
            continue

        html     = item.get("table_body", "") or item.get("text", "")
        page_num = item.get("page_idx", 0) + 1 + page_offset

        if not html:
            continue

        try:
            dfs = pd.read_html(io.StringIO(html))
            if not dfs:
                continue

            df         = dfs[0]
            df.columns = [str(c) for c in df.columns]
            df         = df.fillna("")

            tables.append({
                "headers":  list(df.columns),
                "rows":     df.to_dict(orient="records"),
                "num_rows": len(df),
                "num_cols": len(df.columns),
                "source":   "mineru_table",
                "page":     page_num,
            })
        except Exception as e:
            logger.warning(
                "Table HTML parse failed in '%s' page %d: %s",
                source_name, page_num, e,
            )

    return tables


# ══════════════════════════════════════════════════════════════
# MARKDOWN TABLE EXTRACTOR — from MinerU MM_MD text output
# ══════════════════════════════════════════════════════════════

def _extract_markdown_tables(markdown_text: str, source_name: str) -> list:
    """
    Parse pipe-style markdown tables from MinerU's MM_MD text output.

    MinerU emits the same table in both CONTENT_LIST (HTML) and MM_MD
    (markdown).  The markdown version often preserves year-label column
    headers (e.g. "FY2023 / FY2022") better than the HTML form, which
    can collapse merged cells or lose header text.

    Calling this alongside _mineru_extract_tables() and then
    deduplicating gives the widest possible table coverage without
    hardcoding any field names.

    Format detected:
        | Header A   | Header B   |
        | ---------- | ---------- |
        | value 1    | value 2    |
    """
    if not markdown_text:
        return []

    tables = []
    lines  = markdown_text.split("\n")
    i      = 0

    while i < len(lines):
        line = lines[i].strip()

        # A markdown table row starts and ends with '|' and has ≥ 3 pipes
        if not (line.startswith("|") and line.count("|") >= 3):
            i += 1
            continue

        # Collect all consecutive pipe-line rows belonging to this table
        table_lines: list[str] = []
        j = i
        while j < len(lines):
            tl = lines[j].strip()
            if tl.startswith("|") and "|" in tl[1:]:
                table_lines.append(tl)
                j += 1
            else:
                break
        i = j  # advance outer pointer past this table block

        if len(table_lines) < 3:
            continue

        # Locate the separator row (cells are only dashes/colons/spaces)
        sep_idx: int | None = None
        for k, tl in enumerate(table_lines):
            inner = tl.strip("|").split("|")
            if inner and all(
                re.match(r"^[-:\s]+$", cell.strip())
                for cell in inner
                if cell.strip()
            ):
                sep_idx = k
                break

        if sep_idx is None or sep_idx == 0:
            continue

        # Headers = the row immediately before the separator
        raw_header_cells = table_lines[sep_idx - 1].split("|")
        # strip the outer empty strings produced by leading/trailing '|'
        if raw_header_cells and not raw_header_cells[0].strip():
            raw_header_cells = raw_header_cells[1:]
        if raw_header_cells and not raw_header_cells[-1].strip():
            raw_header_cells = raw_header_cells[:-1]
        headers = [c.strip() for c in raw_header_cells]

        if not headers:
            continue

        # Data rows come after the separator
        data_rows: list[dict] = []
        for tl in table_lines[sep_idx + 1:]:
            raw_cells = tl.split("|")
            if raw_cells and not raw_cells[0].strip():
                raw_cells = raw_cells[1:]
            if raw_cells and not raw_cells[-1].strip():
                raw_cells = raw_cells[:-1]
            cells = [c.strip() for c in raw_cells]

            # Pad/trim to match the number of headers
            while len(cells) < len(headers):
                cells.append("")
            cells = cells[: len(headers)]

            row_dict = {headers[k]: cells[k] for k in range(len(headers))}
            data_rows.append(row_dict)

        if data_rows:
            tables.append({
                "headers":  headers,
                "rows":     data_rows,
                "num_rows": len(data_rows),
                "num_cols": len(headers),
                "source":   "mineru_md_table",
                "page":     0,  # page assigned by caller after extraction
            })

    return tables


# ══════════════════════════════════════════════════════════════
# PYMUPDF SUPPLEMENTARY TABLE EXTRACTOR
# ══════════════════════════════════════════════════════════════

def _extract_pymupdf_tables(filepath: str, source_name: str) -> list:
    """
    Extract tables using PyMuPDF's geometric line/cell detection.

    PyMuPDF's find_tables() identifies table structures via ruling lines,
    text alignment, and whitespace geometry — complementary to MinerU's
    AI layout analysis.  For clean digital PDFs, PyMuPDF often recovers
    tables that MinerU either missed or produced with generic col headers.

    Dynamic header promotion:
        When PyMuPDF auto-generates generic column names (Col0, Col1…),
        the first data row is inspected.  If it contains year labels or
        non-numeric text that looks like a header row, it is promoted to
        column names so downstream year detection works correctly.
    """
    tables: list[dict] = []
    try:
        import fitz
        doc = fitz.open(filepath)

        for page_num, page in enumerate(doc):
            try:
                if not hasattr(page, "find_tables"):
                    break   # PyMuPDF < 1.23 — method not available

                finder = page.find_tables()
                if not finder or not finder.tables:
                    continue

                for tbl in finder.tables:
                    try:
                        df = tbl.to_pandas()
                        if df is None or df.empty:
                            continue

                        raw_headers = [str(c).strip() for c in df.columns]

                        # Detect fully generic headers (Col0, Col1, …)
                        all_generic = all(
                            re.match(r"^col\d+$", h, re.IGNORECASE) or not h
                            for h in raw_headers
                        )

                        if all_generic and len(df) > 0:
                            # Try to promote the first data row to headers
                            first_vals = df.iloc[0].tolist()
                            promoted = [
                                str(v).strip() if str(v).strip() else f"col_{k}"
                                for k, v in enumerate(first_vals)
                            ]
                            df = df.iloc[1:].reset_index(drop=True)
                            raw_headers = promoted

                        df.columns = raw_headers
                        df = df.fillna("")
                        rows = df.to_dict(orient="records")

                        if not rows:
                            continue

                        tables.append({
                            "headers":  raw_headers,
                            "rows":     rows,
                            "num_rows": len(rows),
                            "num_cols": len(raw_headers),
                            "source":   "pymupdf_table",
                            "page":     page_num + 1,
                        })
                    except Exception:
                        pass   # skip individual table errors silently

            except Exception:
                pass   # skip individual page errors silently

        doc.close()

    except Exception as e:
        logger.warning(
            "PyMuPDF supplementary table extraction failed for '%s': %s",
            source_name, e,
        )

    return tables


# ══════════════════════════════════════════════════════════════
# TABLE DEDUPLICATION — across multi-source merges
# ══════════════════════════════════════════════════════════════

def _year_pattern_in_text(text: str) -> bool:
    """Return True if the string contains a recognisable year label."""
    t = text.lower().strip()
    return bool(
        re.search(r"\bfy\s*20\d{2}\b", t)
        or re.search(r"\b20\d{2}[-/]\d{2}", t)
        or re.search(r"\bmarch\s+20\d{2}", t)
        or re.search(r"\bmar\s+20\d{2}", t)
        or re.search(r"\b20\d{2}\b", t)
    )


def _deduplicate_tables(tables: list) -> list:
    """
    Remove near-duplicate tables from a merged multi-source table list.

    Two tables are treated as duplicates when ALL of:
      - Same number of columns
      - Same first-column header text (case-insensitive, stripped)
      - Row counts within 10% of each other

    When a duplicate pair is found, the version with MORE year-like labels
    in its column headers is kept.  On a tie, source priority breaks it:
        pymupdf_table > mineru_md_table > mineru_table (other)

    This is purely structural — no financial field names are hardcoded.
    """
    if not tables:
        return []

    SOURCE_PRIORITY = {
        "pymupdf_table":    1,
        "mineru_md_table":  2,
    }

    def _year_score(t: dict) -> int:
        return sum(1 for h in (t.get("headers") or []) if _year_pattern_in_text(str(h)))

    def _src_prio(t: dict) -> int:
        return SOURCE_PRIORITY.get(t.get("source", ""), 3)

    def _are_dup(a: dict, b: dict) -> bool:
        if a.get("num_cols") != b.get("num_cols"):
            return False
        a_h0 = ((a.get("headers") or [""])[0] or "").lower().strip()
        b_h0 = ((b.get("headers") or [""])[0] or "").lower().strip()
        if a_h0 != b_h0:
            return False
        ar = a.get("num_rows", 0)
        br = b.get("num_rows", 0)
        mx = max(ar, br)
        if mx == 0:
            return True
        return abs(ar - br) / mx <= 0.10

    unique: list[dict] = []
    for t in tables:
        replaced = False
        for k, existing in enumerate(unique):
            if _are_dup(t, existing):
                # Keep the richer version: more year labels, then source prio
                if (_year_score(t), -_src_prio(t)) > (_year_score(existing), -_src_prio(existing)):
                    unique[k] = t
                replaced = True
                break
        if not replaced:
            unique.append(t)

    return unique


# ══════════════════════════════════════════════════════════════
# MINERU PARSER — PDF / images
# ══════════════════════════════════════════════════════════════

def _parse_with_mineru(filepath: str) -> dict:
    """
    Parse a PDF or image file using MinerU's CPU pipeline backend.

    Memory strategy — page batching:
        MinerU accepts start_page_id / end_page_id so we can restrict each
        call to PDF_BATCH_SIZE pages.  convert_pdf_bytes_to_bytes_by_pypdfium2
        slices the bytes in-memory (no temp file) before each call.
        After each batch we call gc.collect() to release image buffers.

    Table extraction:
        MinerU outputs tables as HTML in its content_list JSON.
        _mineru_extract_tables() converts these to pandas DataFrames and then
        to the pipeline's standard table-dict format.

    Output:
        text     : Markdown string (## headings preserved).
        tables   : List of table dicts tagged source="mineru_table".
        sections : Chunked via DocumentLoader.preprocess_document().
    """
    name = Path(filepath).name
    ext  = Path(filepath).suffix.lower()

    # ── Import MinerU internals ────────────────────────────────
    try:
        from mineru.cli.common import (
            read_fn,
            convert_pdf_bytes_to_bytes_by_pypdfium2,
        )
        from mineru.data.data_reader_writer import FileBasedDataWriter
        from mineru.utils.enum_class import MakeMode
        from mineru.backend.pipeline.pipeline_analyze import (
            doc_analyze,
        )
        from mineru.backend.pipeline.pipeline_middle_json_mkcontent import (
            union_make,
        )
        from mineru.backend.pipeline.model_json_to_middle_json import (
            result_to_middle_json,
        )
    except ImportError as imp_err:
        err = (
            f"MinerU is not installed: {imp_err}. "
            "Install with:  pip install 'mineru[all]'"
        )
        logger.error(err)
        return _empty_result(filepath, ext, [err])

    all_texts:  list[str]  = []
    all_tables: list[dict] = []
    all_errors: list[str]  = []

    # ── Count total pages (PDFs only; images = 1 page) ────────
    if ext == ".pdf":
        import fitz
        _src = fitz.open(filepath)
        total_pages = _src.page_count
        _src.close()
    else:
        total_pages = 1

    # ── Read source bytes once ─────────────────────────────────
    try:
        pdf_bytes_orig = read_fn(filepath)
    except Exception as e:
        err = f"Failed to read '{name}': {e}"
        logger.error(err)
        return _empty_result(filepath, ext, [err])

    # ── Temp dir for MinerU image output ──────────────────────
    tmp_dir = tempfile.mkdtemp(prefix="mineru_")

    try:
        for batch_start in range(0, total_pages, PDF_BATCH_SIZE):
            batch_end = min(batch_start + PDF_BATCH_SIZE, total_pages)
            logger.info(
                "MinerU '%s': pages %d–%d of %d",
                name, batch_start + 1, batch_end, total_pages,
            )

            # Slice page range in-memory (PDF only; images pass through)
            if ext == ".pdf" and total_pages > PDF_BATCH_SIZE:
                try:
                    batch_bytes = convert_pdf_bytes_to_bytes_by_pypdfium2(
                        pdf_bytes_orig, batch_start, batch_end - 1
                    )
                except Exception as slice_err:
                    err = (
                        f"Page slice {batch_start+1}–{batch_end} failed "
                        f"for '{name}': {slice_err}"
                    )
                    logger.error(err)
                    all_errors.append(err)
                    continue
            else:
                batch_bytes = pdf_bytes_orig

            # Each batch gets its own image output directory
            batch_img_dir = os.path.join(tmp_dir, f"img_{batch_start}")
            os.makedirs(batch_img_dir, exist_ok=True)
            image_writer = FileBasedDataWriter(batch_img_dir)

            try:
                infer_results, all_image_lists, all_pdf_docs, lang_list, ocr_enabled_list = (
                    doc_analyze(
                        [batch_bytes],
                        ["en"],
                        parse_method="auto",
                        formula_enable=False,
                        table_enable=True,
                    )
                )

                middle_json = result_to_middle_json(
                    infer_results[0],
                    all_image_lists[0],
                    all_pdf_docs[0],
                    image_writer,
                    lang_list[0],
                    ocr_enabled_list[0],
                    False,          # formula_enable
                )

                pdf_info = middle_json["pdf_info"]

                # ── Markdown text ──────────────────────────────
                batch_text = union_make(pdf_info, MakeMode.MM_MD, "")
                if batch_text:
                    all_texts.append(batch_text)

                # ── Tables (HTML from CONTENT_LIST) ────────────
                content_list = union_make(pdf_info, MakeMode.CONTENT_LIST, "")
                all_tables.extend(
                    _mineru_extract_tables(content_list, name, page_offset=batch_start)
                )

                # ── Tables (markdown from MM_MD text) ──────────
                # MinerU's markdown output often preserves year-label
                # column headers better than the HTML CONTENT_LIST form.
                # Extracting from both sources maximises table coverage;
                # _deduplicate_tables() removes overlaps after all batches.
                if batch_text:
                    md_tables = _extract_markdown_tables(batch_text, name)
                    for mt in md_tables:
                        mt["page"] = batch_start + 1
                    all_tables.extend(md_tables)

            except Exception as conv_err:
                err = (
                    f"MinerU conversion failed for batch pages "
                    f"{batch_start+1}–{batch_end} of '{name}': {conv_err}"
                )
                logger.error(err)
                all_errors.append(err)

            gc.collect()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── Supplementary: PyMuPDF geometric table extraction ─────
    # PyMuPDF's find_tables() uses line/cell geometry — it catches tables
    # that MinerU's AI detection missed (e.g. borderless financial tables).
    # Only run for PDFs; images are handled entirely by MinerU's OCR.
    if ext == ".pdf":
        pym_tables = _extract_pymupdf_tables(filepath, name)
        if pym_tables:
            logger.info(
                "PyMuPDF found %d supplementary table(s) in '%s'",
                len(pym_tables), name,
            )
        all_tables.extend(pym_tables)

    # ── Deduplicate across all sources ────────────────────────
    # Merges HTML, markdown, and PyMuPDF tables; keeps the version
    # with the richest column headers (most year labels) for each
    # logically identical table.
    all_tables = _deduplicate_tables(all_tables)

    full_text = "\n\n".join(all_texts)
    loader    = DocumentLoader(filepath)
    sections  = loader.preprocess_document(full_text) if full_text else []

    return {
        "text":     full_text,
        "sections": sections,
        "tables":   all_tables,
        "metadata": {
            "filepath":          filepath,
            "extension":         ext,
            "section_count":     len(sections),
            "char_count":        len(full_text),
            "table_count":       len(all_tables),
            "extraction_errors": all_errors,
        },
    }


# ══════════════════════════════════════════════════════════════
# DOCX PARSER — python-docx
# ══════════════════════════════════════════════════════════════

def _parse_docx(filepath: str) -> dict:
    name = Path(filepath).name
    extraction_errors: list[str] = []
    text   = ""
    tables: list[dict] = []

    try:
        import docx as _docx

        doc        = _docx.Document(filepath)
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        para_text  = "\n\n".join(paragraphs)

        table_text_blocks = []   # ← ADD THIS

        for t_idx, table in enumerate(doc.tables):
            try:
                rows_data = [
                    [cell.text.strip() for cell in row.cells]
                    for row in table.rows
                ]
                if not rows_data:
                    continue

                headers = rows_data[0]
                records = []
                for row in rows_data[1:]:
                    records.append({
                        headers[i] if i < len(headers) else f"col_{i}": v
                        for i, v in enumerate(row)
                    })

                tables.append({
                    "headers":  headers,
                    "rows":     records,
                    "num_rows": len(records),
                    "num_cols": len(headers),
                    "source":   "docx_table",
                    "page":     1,
                })

                # ── ADD: render table as pipe-delimited text ──────
                header_line = " | ".join(headers)
                row_lines   = [
                    " | ".join(str(r.get(h, "")) for h in headers)
                    for r in records
                ]
                table_text_blocks.append(header_line + "\n" + "\n".join(row_lines))
                # ─────────────────────────────────────────────────

            except Exception as te:
                err = f"DOCX table {t_idx} extraction failed in '{name}': {te}"
                logger.warning(err)
                extraction_errors.append(err)

        # ── CHANGE: combine paragraphs + table text ───────────────
        all_parts = [p for p in [para_text] + table_text_blocks if p.strip()]
        text = "\n\n".join(all_parts)   # ← was just para_text

     

    except Exception as e:
        err = f"python-docx failed for '{name}': {e}"
        logger.error(err)
        extraction_errors.append(err)

    loader   = DocumentLoader(filepath)
    sections = loader.preprocess_document(text) if text else []

    return {
        "text":     text,
        "sections": sections,
        "tables":   tables,
        "metadata": {
            "filepath":          filepath,
            "extension":         ".docx",
            "section_count":     len(sections),
            "char_count":        len(text),
            "table_count":       len(tables),
            "extraction_errors": extraction_errors,
        },
    }


# ══════════════════════════════════════════════════════════════
# DOCUMENTLOADER FALLBACK — .doc / .txt / unsupported formats
# ══════════════════════════════════════════════════════════════

def _parse_with_document_loader(filepath: str) -> dict:
    """
    Parse .doc and .txt files via DocumentLoader (Tika / plain read).
    MinerU does not support the legacy .doc format.
    """
    name = Path(filepath).name
    ext  = Path(filepath).suffix.lower()
    extraction_errors = []
    text     = ""
    sections = []

    loader = DocumentLoader(filepath)
    try:
        docs     = list(loader.lazy_load(lcdocument=True))
        text     = loader.format_docs(docs) if docs else ""
        sections = loader.preprocess_document(text) if text else []
    except Exception as e:
        err = f"DocumentLoader raised an unhandled exception for '{name}': {e}"
        logger.error(err)
        extraction_errors.append(err)

    extraction_errors.extend(loader._extraction_errors)

    return {
        "text":     text,
        "sections": sections,
        "tables":   [],
        "metadata": {
            "filepath":          filepath,
            "extension":         ext,
            "section_count":     len(sections),
            "char_count":        len(text),
            "table_count":       0,
            "extraction_errors": extraction_errors,
        },
    }


# ══════════════════════════════════════════════════════════════
# CSV EXTRACTOR
# ══════════════════════════════════════════════════════════════

def _parse_csv(filepath: str) -> dict:
    """
    Parse a .csv file using the csv module.

    Treats the first row as headers. All remaining rows become the data.
    Returns a single structured table and a plain-text representation.
    """
    import csv

    name   = Path(filepath).name
    errors = []
    tables = []
    text   = ""

    try:
        with open(filepath, newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh)
            rows   = [row for row in reader if any(cell.strip() for cell in row)]

        if not rows:
            return _empty_result(filepath, ".csv", ["CSV file is empty"])

        headers  = [h.strip() for h in rows[0]]
        data_rows = []
        for row in rows[1:]:
            # Pad / trim to match header count
            while len(row) < len(headers):
                row.append("")
            row = row[: len(headers)]
            data_rows.append({headers[i]: row[i].strip() for i in range(len(headers))})

        if data_rows:
            tables.append({
                "headers":  headers,
                "rows":     data_rows,
                "num_rows": len(data_rows),
                "num_cols": len(headers),
                "source":   "csv_table",
            })

        # Build plain-text representation
        header_line = " | ".join(headers)
        row_lines   = [" | ".join(r.get(h, "") for h in headers) for r in data_rows]
        text        = header_line + "\n" + "\n".join(row_lines)

    except Exception as e:
        err = f"CSV parsing failed for '{name}': {e}"
        logger.error(err)
        errors.append(err)

    loader   = DocumentLoader(filepath)
    sections = loader.preprocess_document(text) if text else []

    return {
        "text":     text,
        "sections": sections,
        "tables":   tables,
        "metadata": {
            "filepath":          filepath,
            "extension":         ".csv",
            "section_count":     len(sections),
            "char_count":        len(text),
            "table_count":       len(tables),
            "extraction_errors": errors,
        },
    }


# ══════════════════════════════════════════════════════════════
# EXCEL HELPER — unnamed column resolver
# ══════════════════════════════════════════════════════════════

def _resolve_unnamed_columns(columns: list) -> list:
    """
    Replace pandas "Unnamed: N" column labels with meaningful names.

    pandas names merged or blank header cells as "Unnamed: N" when
    reading Excel files. Strategy:
      1. Forward-fill: an "Unnamed: N" column inherits the last real
         column name with a positional suffix (_2, _3, …).
         Example: ["FY2024", "Unnamed: 1", "Unnamed: 2"]
                → ["FY2024", "FY2024_2", "FY2024_3"]
      2. Leading unnamed columns fall back to "col_N" positional names.
      3. After forward-filling, remaining duplicates are made unique
         with a _N suffix.
    """
    resolved      = []
    last_real     = None
    inherit_count = {}

    for col_i, col in enumerate(columns):
        if col.startswith("Unnamed:"):
            if last_real is not None:
                inherit_count[last_real] = inherit_count.get(last_real, 1) + 1
                resolved.append(f"{last_real}_{inherit_count[last_real]}")
            else:
                resolved.append(f"col_{col_i}")
        else:
            resolved.append(col)
            last_real = col
            inherit_count[col] = 1

    seen: dict = {}
    final = []
    for name in resolved:
        if name in seen:
            seen[name] += 1
            final.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 1
            final.append(name)

    return final


# ══════════════════════════════════════════════════════════════
# EXCEL EXTRACTOR
# ══════════════════════════════════════════════════════════════

def _parse_excel(filepath: str) -> dict:
    """
    Parse Excel files (.xlsx / .xls) using pandas.
    All sheets are read. Each non-empty sheet becomes a text block
    and a structured table dict.
    """
    name       = Path(filepath).name
    text_parts = []
    tables     = []
    errors     = []

    try:
        sheets = pd.read_excel(filepath, sheet_name=None)
    except Exception as e:
        err = f"pandas failed to open Excel file '{name}': {e}"
        logger.error(err)
        return {
            "text":     "",
            "sections": [],
            "tables":   [],
            "metadata": {
                "filepath":          filepath,
                "extension":         Path(filepath).suffix.lower(),
                "section_count":     0,
                "char_count":        0,
                "table_count":       0,
                "extraction_errors": [err],
            },
        }

    for sheet_name, df in sheets.items():
        try:
            df.dropna(how="all", inplace=True)
            df.reset_index(drop=True, inplace=True)

            if df.empty:
                continue

            df.columns = [str(c) for c in df.columns]
            df         = df.fillna("")
            df.columns = _resolve_unnamed_columns(list(df.columns))

            text_parts.append(f"## {sheet_name}\n{df.to_string(index=False)}")

            tables.append({
                "sheet":    sheet_name,
                "headers":  list(df.columns),
                "rows":     df.to_dict(orient="records"),
                "num_rows": len(df),
                "num_cols": len(df.columns),
                "source":   "excel_sheet",
            })

        except Exception as sheet_err:
            err = f"Failed to parse sheet '{sheet_name}' in '{name}': {sheet_err}"
            logger.warning(err)
            errors.append(err)

    text = "\n\n".join(text_parts)

    return {
        "text":     text,
        "sections": text_parts,
        "tables":   tables,
        "metadata": {
            "filepath":          filepath,
            "extension":         Path(filepath).suffix.lower(),
            "section_count":     len(text_parts),
            "char_count":        len(text),
            "table_count":       len(tables),
            "extraction_errors": errors,
        },
    }


# ══════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════

def _empty_result(filepath: str, ext: str, errors: list) -> dict:
    """Return an empty result dict with the given errors."""
    return {
        "text":     "",
        "sections": [],
        "tables":   [],
        "metadata": {
            "filepath":          filepath,
            "extension":         ext,
            "section_count":     0,
            "char_count":        0,
            "table_count":       0,
            "extraction_errors": errors,
        },
    }


# ══════════════════════════════════════════════════════════════
# CACHE MANAGEMENT
# ══════════════════════════════════════════════════════════════

def clear_cache() -> None:
    """
    Clear the in-memory parse cache.

    Called by main.py at the start of each pipeline run to ensure
    fresh parsing when input_docs/ contents have changed between runs.
    """
    global _parse_cache
    cleared = len(_parse_cache)
    _parse_cache = {}
    logger.debug("parse_document cache cleared (%d entries removed).", cleared)
