# tools/document_loader.py
# ──────────────────────────────────────────────────────────────
# Multi-format document loader used by all pipeline nodes via
# parse_document() in docling_reader.py.
#
# Supported formats and their extraction strategy:
#   PDF (native text)  → PyMuPDF page.get_text() per page
#   PDF (scanned page) → PyMuPDF pixmap render → EasyOCR
#   .docx              → python-docx paragraphs + doc.tables
#   .doc               → Apache Tika (requires Java) + HTML cleanup
#   .txt               → direct file read (utf-8 / latin-1 fallback)
#   image              → EasyOCR via OcrExtractor
#
# Changes from previous version:
#
#   Gap 1 — .docx tables never extracted:
#     load_word_file() only read doc.paragraphs. Financial .docx files
#     store most of their data in doc.tables, which was silently dropped.
#     Fix: doc.tables is now iterated. Each table is extracted into
#     self._docx_tables in the same dict format as PyMuPDF/Excel tables.
#     Table text is also rendered as pipe-delimited lines and merged
#     into the returned text string for keyword/LLM use.
#     docling_reader.parse_document() reads self._docx_tables and
#     stores them in the result dict under "tables".
#
#   Gap 2 — .docx paragraph sectioning broken:
#     load_word_file() joined paragraphs with '\n' (single newline).
#     preprocess_document() Strategy 2 splits on '\n{2,}', so the
#     entire document collapsed into one block and fell through to
#     the 500-word word-split fallback, losing paragraph structure.
#     Fix: paragraphs are now joined with '\n\n' (double newline).
#
#   Gap 4 — Tika .doc output contains HTML tags and entities:
#     Tika returns HTML-formatted content for malformed .doc files,
#     embedding <p>, <body> tags and entities like &amp;, &lt;.
#     Nothing downstream cleaned these — they polluted LLM prompts.
#     Fix: _strip_html() is called on Tika output. It uses HTMLParser
#     to strip tags and html.unescape() to decode entities, then
#     collapses excess blank lines to preserve paragraph structure.
#
#   Previous fixes (carried forward):
#   Gap 3 (old) — scanned PDF OCR failures now collected in
#     self._extraction_errors and surfaced to state["errors"].
#   Gap 5 (old) — Tika failures collected in _extraction_errors.
#   Gap 6 (old) — temp PNG files deleted in finally block.
#   fitz.open() wrapped for corrupt/password-protected PDFs.
#   load_word_file() wrapped in try/except for corrupt .docx.
#   lazy_load() outer exception appended to _extraction_errors.
#   0-page PDFs handled gracefully.
# ──────────────────────────────────────────────────────────────

import warnings
warnings.filterwarnings('ignore', message='.*pin_memory.*')

import os
import re
import html
import logging
from html.parser import HTMLParser

os.environ['TIKA_LOG_PATH'] = os.path.join(os.getcwd(), 'logs')
os.makedirs(os.environ['TIKA_LOG_PATH'], exist_ok=True)

from typing import Iterator, Union, List, Iterable
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document as LCDocument
import fitz
from pathlib import Path
import docx
from tika import parser
from utils.ocr_extractor import OcrExtractor

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# MODULE-LEVEL HELPERS
# ══════════════════════════════════════════════════════════════

class _HTMLStripper(HTMLParser):
    """Minimal HTMLParser subclass that collects non-tag text."""
    def __init__(self):
        super().__init__()
        self._parts: list = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return ''.join(self._parts)


def _strip_html(text: str) -> str:
    """
    Remove HTML tags and decode HTML entities from a string.

    Used to clean Tika output for .doc files. Tika sometimes wraps
    content in <html><body><p> tags and encodes characters as &amp;,
    &lt;, &gt;, &nbsp;, etc. when processing malformed legacy files.

    Steps:
      1. HTMLParser strips all tags, retaining only inner text data.
      2. html.unescape() converts &amp; → &, &lt; → <, &nbsp; → space, etc.
      3. Consecutive blank lines are collapsed to a single blank line
         so the output has the same double-newline paragraph structure
         as every other format, allowing preprocess_document() Strategy 2
         to split it correctly.
    """
    stripper = _HTMLStripper()
    stripper.feed(text)
    clean = html.unescape(stripper.get_text())
    # Collapse 3+ consecutive newlines → 2 (preserve paragraph breaks)
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    return clean.strip()


class DocumentLoader(BaseLoader):

    def __init__(self, file_paths: Union[str, List[str]]) -> None:
        self._file_paths = [file_paths] if isinstance(file_paths, str) else file_paths
        self._image_extractor = OcrExtractor()

        # Errors collected during lazy_load() / _extract_pdf() / load_doc_file().
        # docling_reader.parse_document() reads this after calling lazy_load()
        # and merges the list into the result metadata so Node 3 can propagate
        # them to state["errors"] with full context.
        self._extraction_errors: List[str] = []

        # Scanned PDF page OCR lines, keyed by 0-based page index.
        # Populated by _extract_pdf() for every page that required OCR.
        # docling_reader.parse_document() reads this to run heuristic
        # table detection on scanned pages (Gap 4 fix lives there).
        self._scanned_page_ocr_lines: dict = {}   # {page_number_0based: [str]}

        # Structured tables extracted from .docx files via python-docx.
        # python-docx exposes doc.tables separately from doc.paragraphs.
        # load_word_file() populates this so parse_document() can store
        # them in the result alongside paragraph text (Gap 1 fix).
        self._docx_tables: List[dict] = []

    # ── File type routing ─────────────────────────────────────

    def get_file_type(self, file_path: str) -> str:
        """Return a routing key for the given file extension."""
        suffix = Path(file_path).suffix.lower()
        routing = {
            '.pdf':  'pdf',
            '.png':  'image',
            '.jpg':  'image',
            '.jpeg': 'image',
            '.bmp':  'image',
            '.tiff': 'image',
            '.docx': 'docx',
            '.doc':  'doc',
            '.txt':  'txt',
            '.csv':  'csv',
        }
        if suffix not in routing:
            raise ValueError(f"Unsupported file type: {suffix}")
        return routing[suffix]

    # ── Scanned-page detection ────────────────────────────────

    def is_scanned_page(self, page: fitz.Page) -> bool:
        """
        Return True if a PDF page appears to be image-based (scanned).
        Pages with fewer than 50 embedded characters are treated as scanned
        and routed to EasyOCR for text extraction.
        """
        return len(page.get_text().strip()) < 50

    # ── PDF extraction ────────────────────────────────────────

    def _extract_pdf(self, source: str) -> str:
        """
        Extract text from a PDF using PyMuPDF, page by page.

        Per-page strategy:
          Native text (>= 50 chars) → page.get_text() directly.
          Scanned (<  50 chars)     → render PNG at 150 DPI → EasyOCR.

        OCR lines for scanned pages are stored in
        self._scanned_page_ocr_lines for downstream table heuristics.

        Temp PNG files are always deleted in a finally block (Gap 6).
        OCR errors per page are collected in self._extraction_errors
        instead of being silently dropped (Gap 3).
        """
        name = Path(source).name

        try:
            doc = fitz.open(source)
        except Exception as e:
            err = f"Failed to open PDF '{name}': {e}"
            logger.error(err)
            self._extraction_errors.append(err)
            return ""

        if doc.page_count == 0:
            self._extraction_errors.append(
                f"PDF '{name}' has 0 pages — file may be corrupt or empty."
            )
            doc.close()
            return ""

        pages_text = []

        for page in doc:
            if self.is_scanned_page(page):
                # ── Scanned page: render to PNG → OCR ─────────
                tmp_path = None
                try:
                    pix      = page.get_pixmap(dpi=150)
                    tmp_path = source + f"_page{page.number}_ocr_tmp.png"
                    pix.save(tmp_path)
                    ocr_lines = self._image_extractor.extract(tmp_path)
                    # Store lines for Gap 4 table heuristic in docling_reader
                    self._scanned_page_ocr_lines[page.number] = ocr_lines
                    text = "\n".join(ocr_lines)
                except Exception as ocr_err:
                    err = (
                        f"OCR failed on page {page.number + 1} of '{name}': "
                        f"{ocr_err}"
                    )
                    logger.warning(err)
                    self._extraction_errors.append(err)
                    text = ""
                finally:
                    # Always clean up the temp PNG even if OCR raised (Gap 6)
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError as rm_err:
                            logger.warning(
                                "Could not delete temp file '%s': %s",
                                tmp_path, rm_err,
                            )
            else:
                # ── Native text page ──────────────────────────
                text = page.get_text().strip()

            if text.strip():
                pages_text.append(text)

        doc.close()
        result = "\n\n".join(pages_text)
        logger.debug("Extracted %d chars from '%s'.", len(result), name)
        return result

    # ── Non-PDF loaders ───────────────────────────────────────

    def load_txt_file(self, file_path: str) -> str:
        """Read a plain-text file. Falls back to latin-1 on UTF-8 errors."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            with open(file_path, 'r', encoding='latin-1') as f:
                return f.read()

    def load_csv_file(self, file_path: str) -> str:
        """
        Extract text from a .csv file using the csv module.

        Treats the first non-empty row as column headers.
        Each data row is rendered as a pipe-delimited line so that
        downstream keyword scoring and LLM prompts can read it the
        same way as DOCX table text.
        """
        import csv

        name = Path(file_path).name
        try:
            with open(file_path, newline='', encoding='utf-8', errors='replace') as fh:
                reader = csv.reader(fh)
                rows   = [row for row in reader if any(cell.strip() for cell in row)]

            if not rows:
                return ''

            headers   = [h.strip() for h in rows[0]]
            data_rows = rows[1:]

            lines = [' | '.join(headers)]
            for row in data_rows:
                # Pad / trim to match header count
                while len(row) < len(headers):
                    row.append('')
                row = row[: len(headers)]
                lines.append(' | '.join(cell.strip() for cell in row))

            return '\n'.join(lines)

        except Exception as e:
            err = f"CSV extraction failed for '{name}': {e}"
            logger.error(err)
            self._extraction_errors.append(err)
            return ''

    def load_doc_file(self, file_path: str) -> str:
        """
        Extract text from a legacy .doc file using Apache Tika.

        Requires Java to be installed and Tika server to be reachable.
        Errors are appended to self._extraction_errors instead of being
        silently swallowed (Gap 5 fix).

        Gap 4 fix — Tika HTML cleanup:
          Tika sometimes returns HTML-formatted content for malformed .doc
          files, embedding <html>, <body>, <p> tags and HTML entities like
          &amp;, &lt;, &gt; directly in the content string. Nothing
          downstream cleans these — they pollute every LLM prompt that
          receives .doc text.
          Fix: html.parser strips all tags; html.unescape decodes entities.
          Consecutive whitespace is then normalised so the output is the
          same clean plain text that would come from a well-formed file.
        """
        name = Path(file_path).name
        try:
            parsed  = parser.from_file(file_path)
            content = parsed.get('content')
            if not content:
                err = (
                    f"Apache Tika returned empty content for '{name}'. "
                    "Verify that Java is installed and Tika server is running."
                )
                logger.warning(err)
                self._extraction_errors.append(err)
                return ""

            # ── Strip HTML tags and decode entities (Gap 4) ──────
            content = _strip_html(content)
            return content

        except Exception as e:
            err = (
                f"Apache Tika extraction failed for '{name}': {e}. "
                "Verify that Java is installed and the Tika server is accessible."
            )
            logger.error(err)
            self._extraction_errors.append(err)
            return ""

    def load_word_file(self, file_path: str) -> str:
        """
        Extract text and tables from a .docx file using python-docx.

        Gap 2 fix — paragraph joining with double newline:
          The previous version joined paragraphs with a single '\\n'.
          preprocess_document() Strategy 2 splits on '\\n{2,}' to detect
          paragraph boundaries. With single-\\n joining, the entire document
          collapsed into one block and fell through to the flat 500-word
          word-split fallback, losing all paragraph structure.
          Fix: paragraphs are now joined with '\\n\\n' so Strategy 2
          produces structurally coherent chunks aligned with the document's
          own paragraph boundaries.

        Gap 1 fix — .docx table extraction:
          python-docx exposes doc.tables separately from doc.paragraphs.
          The previous version never read doc.tables, so all structured
          table data in .docx financial statements was silently dropped.
          Fix: every table in doc.tables is extracted into self._docx_tables
          in the same dict format used by PyMuPDF and Excel extractors.
          Table text is also rendered as pipe-delimited lines and merged
          into the returned text string so it appears in extracted_texts
          for keyword scoring and LLM prompts.
        """
        name = Path(file_path).name
        try:
            doc = docx.Document(file_path)

            # ── Paragraphs: double-newline join (Gap 2) ───────────
            para_text = '\n\n'.join(
                p.text for p in doc.paragraphs if p.text.strip()
            )

            # ── Tables: extract structured + text form (Gap 1) ────
            table_text_blocks = []

            for table_index, table in enumerate(doc.tables):
                try:
                    if not table.rows:
                        continue

                    # First row → column headers
                    raw_headers = [
                        cell.text.strip() for cell in table.rows[0].cells
                    ]
                    # Blank headers get a positional fallback name
                    headers = [
                        h if h else f"col_{col_i}"
                        for col_i, h in enumerate(raw_headers)
                    ]

                    rows = []
                    for row in table.rows[1:]:
                        row_dict = {
                            headers[col_i] if col_i < len(headers) else f"col_{col_i}":
                            cell.text.strip()
                            for col_i, cell in enumerate(row.cells)
                        }
                        # Skip rows where every cell is empty
                        if any(v for v in row_dict.values()):
                            rows.append(row_dict)

                    if not rows:
                        continue

                    # Store in structured format for docling_reader to pick up
                    self._docx_tables.append({
                        "headers":     headers,
                        "rows":        rows,
                        "num_rows":    len(rows),
                        "num_cols":    len(headers),
                        "source":      "docx_table",
                        "table_index": table_index,
                    })

                    # Render table as pipe-delimited text for extracted_texts
                    header_line = " | ".join(headers)
                    row_lines   = [
                        " | ".join(str(row_dict.get(h, "")) for h in headers)
                        for row_dict in rows
                    ]
                    table_text_blocks.append(
                        header_line + "\n" + "\n".join(row_lines)
                    )

                except Exception as tbl_err:
                    err = (
                        f"Failed to extract table {table_index} "
                        f"from '{name}': {tbl_err}"
                    )
                    logger.warning(err)
                    self._extraction_errors.append(err)

            # Combine paragraph text and rendered table text
            all_parts = [p for p in [para_text] + table_text_blocks if p.strip()]
            return '\n\n'.join(all_parts)

        except Exception as e:
            err = f"python-docx extraction failed for '{name}': {e}"
            logger.error(err)
            self._extraction_errors.append(err)
            return ""

    # ── LangChain BaseLoader interface ────────────────────────

    def lazy_load(self, lcdocument: bool = True) -> Iterator[Union[LCDocument, str]]:
        """
        Yield one document object (or string) per file path.
        Files that produce empty text are not yielded — callers receive
        an empty iterator for that path, and the root cause is recorded
        in self._extraction_errors.
        """
        for source in self._file_paths:
            try:
                file_type = self.get_file_type(source)

                if file_type == 'image':
                    lines = self._image_extractor.extract(source)
                    text  = '\n'.join(lines)

                elif file_type == 'pdf':
                    text = self._extract_pdf(source)

                elif file_type == 'docx':
                    text = self.load_word_file(source)

                elif file_type == 'doc':
                    text = self.load_doc_file(source)

                elif file_type == 'txt':
                    text = self.load_txt_file(source)

                elif file_type == 'csv':
                    text = self.load_csv_file(source)

                else:
                    text = ""

                if text:
                    if lcdocument:
                        yield LCDocument(page_content=text)
                    else:
                        yield text

            except Exception as e:
                err = f"Unhandled error processing '{source}': {e}"
                logger.error(err)
                self._extraction_errors.append(err)

    def format_docs(self, docs: Iterable[LCDocument]) -> str:
        """Join LCDocument page_content fields into a single string."""
        return "\n\n".join(doc.page_content for doc in docs)

    # ── Text sectioning ───────────────────────────────────────

    def preprocess_document(self, text: str) -> List[str]:
        """
        Split document text into focused sections for LLM prompting.

        Three ordered strategies — first one that produces output wins:

          1. Markdown header split (## / ###)
             Used when Docling was the extractor (produces markdown).
             PyMuPDF plain-text output NEVER contains markdown headers,
             so this strategy is effectively skipped for all PDF files.

          2. Paragraph + 500-word grouping
             PyMuPDF separates pages and paragraphs with double newlines.
             Consecutive paragraphs are grouped into chunks of up to 500
             words so the LLM receives bounded, structurally coherent
             input. Financial line items within one paragraph stay together.

             The original code ran re.sub(r'\\s+', ' ', text) globally
             BEFORE this step, which erased all double-newlines and made
             paragraph detection impossible (Gap 2 fix).

          3. Pure 500-word word split (last resort)
             For single-paragraph or structure-free text.
        """
        # Strip HTML comments — artifact of Docling markdown output.
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL).strip()
        if not text:
            return []

        # ── Strategy 1: Markdown header split ─────────────────
        if re.search(r'^#{2,3} ', text, re.MULTILINE):
            parts = re.split(r'(#{2,3} .+)', text)
            sections = []
            for i in range(1, len(parts), 2):
                header  = parts[i].strip()
                content = parts[i + 1].strip() if i + 1 < len(parts) else ''
                combined = f"{header}\n{content}".strip()
                if combined:
                    sections.append(combined)
            sections = [s for s in sections if s]
            if sections:
                return sections

        # ── Strategy 2: Paragraph-boundary + 500-word grouping ─
        # Split on two or more consecutive newlines (page/paragraph breaks
        # that PyMuPDF inserts between text blocks).
        paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
        if len(paragraphs) > 1:
            chunks        = []
            current_chunk: List[str] = []
            current_words = 0
            chunk_limit   = 500

            for para in paragraphs:
                para_words = len(para.split())
                if current_words + para_words > chunk_limit and current_chunk:
                    chunks.append('\n\n'.join(current_chunk))
                    current_chunk = [para]
                    current_words = para_words
                else:
                    current_chunk.append(para)
                    current_words += para_words

            if current_chunk:
                chunks.append('\n\n'.join(current_chunk))
            return chunks

        # ── Strategy 3: Pure 500-word word split ──────────────
        words      = text.split()
        chunk_size = 500
        return [
            ' '.join(words[i:i + chunk_size])
            for i in range(0, len(words), chunk_size)
            if words[i:i + chunk_size]
        ]