"""Tests for integrated_cam_backend/source_locator.py"""
from __future__ import annotations

import csv
import io
import struct
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from source_locator import SourceLocatorService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
APP_ID = "test-app-001"


def make_service(tmp_path: Path) -> SourceLocatorService:
    return SourceLocatorService(application_id=APP_ID, input_docs_dir=tmp_path)


# ---------------------------------------------------------------------------
# _excel_col (static helper)
# ---------------------------------------------------------------------------
class TestExcelCol:
    def test_a_is_1(self):
        assert SourceLocatorService._excel_col(1) == "A"

    def test_z_is_26(self):
        assert SourceLocatorService._excel_col(26) == "Z"

    def test_aa_is_27(self):
        assert SourceLocatorService._excel_col(27) == "AA"

    def test_ab_is_28(self):
        assert SourceLocatorService._excel_col(28) == "AB"

    def test_b_is_2(self):
        assert SourceLocatorService._excel_col(2) == "B"


# ---------------------------------------------------------------------------
# _stringify (static helper)
# ---------------------------------------------------------------------------
class TestStringify:
    def test_none_returns_none(self):
        assert SourceLocatorService._stringify(None) is None

    def test_integer_float(self):
        assert SourceLocatorService._stringify(500.0) == "500"

    def test_decimal_float(self):
        assert SourceLocatorService._stringify(3.14159) == "3.14159"

    def test_string_stripped(self):
        assert SourceLocatorService._stringify("  hello  ") == "hello"

    def test_integer(self):
        assert SourceLocatorService._stringify(42) == "42"


# ---------------------------------------------------------------------------
# _row_matches (static helper)
# ---------------------------------------------------------------------------
class TestRowMatches:
    def test_matches_exact_target(self):
        assert SourceLocatorService._row_matches(["500", "revenue"], "500", None, None)

    def test_matches_substring_target(self):
        assert SourceLocatorService._row_matches(["revenue_from_operations"], None, "revenue", None)

    def test_matches_year(self):
        assert SourceLocatorService._row_matches(["FY2023", "200"], None, None, "FY2023")

    def test_no_match(self):
        assert not SourceLocatorService._row_matches(["something_else"], "500", None, None)

    def test_empty_row_no_match(self):
        assert not SourceLocatorService._row_matches([], "500", None, None)

    def test_all_none_needles_no_match(self):
        assert not SourceLocatorService._row_matches(["data"], None, None, None)


# ---------------------------------------------------------------------------
# _find_matching_cell
# ---------------------------------------------------------------------------
class TestFindMatchingCell:
    def test_exact_match_returns_column(self):
        row_map = {"A": "500", "B": "revenue", "C": "FY2023"}
        cell = SourceLocatorService._find_matching_cell(row_map, "500", None, None)
        assert cell == "A"

    def test_substring_match(self):
        row_map = {"A": "total_revenue_2023", "B": "other"}
        cell = SourceLocatorService._find_matching_cell(row_map, None, "revenue", None)
        assert cell == "A"

    def test_no_match_returns_none(self):
        row_map = {"A": "foo", "B": "bar"}
        assert SourceLocatorService._find_matching_cell(row_map, "500", None, None) is None


# ---------------------------------------------------------------------------
# _find_document
# ---------------------------------------------------------------------------
class TestFindDocument:
    def test_finds_exact_file(self, tmp_path):
        (tmp_path / "balance_sheet.pdf").write_bytes(b"data")
        svc = make_service(tmp_path)
        result = svc._find_document("balance_sheet.pdf")
        assert result is not None
        assert result.name == "balance_sheet.pdf"

    def test_case_insensitive_match(self, tmp_path):
        (tmp_path / "Balance_Sheet.pdf").write_bytes(b"data")
        svc = make_service(tmp_path)
        result = svc._find_document("balance_sheet.pdf")
        assert result is not None

    def test_partial_name_match(self, tmp_path):
        (tmp_path / "company_balance_sheet_FY2023.pdf").write_bytes(b"data")
        svc = make_service(tmp_path)
        result = svc._find_document("balance_sheet")
        assert result is not None

    def test_nonexistent_file_returns_none(self, tmp_path):
        svc = make_service(tmp_path)
        assert svc._find_document("missing.pdf") is None

    def test_missing_docs_dir_returns_none(self, tmp_path):
        svc = SourceLocatorService(application_id=APP_ID, input_docs_dir=tmp_path / "nonexistent")
        assert svc._find_document("any.pdf") is None


# ---------------------------------------------------------------------------
# locate — image and generic file
# ---------------------------------------------------------------------------
class TestLocateImageAndFile:
    def test_image_returns_file_locator(self, tmp_path):
        (tmp_path / "scan.jpg").write_bytes(b"\xff\xd8\xff\xe0")
        svc = make_service(tmp_path)
        ref = svc.locate("scan.jpg", "revenue", "FY2023", 5000, "ev-01")
        assert ref.locator.type == "file"
        assert ref.locator.label == "Image evidence"

    def test_unknown_extension_returns_file_locator(self, tmp_path):
        (tmp_path / "data.bin").write_bytes(b"\x00\x01")
        svc = make_service(tmp_path)
        ref = svc.locate("data.bin", None, None, None, "ev-02")
        assert ref.locator.type == "file"

    def test_missing_document_still_returns_reference(self, tmp_path):
        svc = make_service(tmp_path)
        ref = svc.locate("nonexistent.pdf", "revenue", "FY2023", 5000, "ev-03")
        assert ref.document_name == "nonexistent.pdf"
        assert ref.locator.type == "unknown"

    def test_hyperlink_constructed(self, tmp_path):
        svc = make_service(tmp_path)
        ref = svc.locate("report.pdf", None, None, None, "ev-04")
        assert ref.hyperlink == f"/api/files/{APP_ID}/report.pdf"

    def test_none_document_name_defaults(self, tmp_path):
        svc = make_service(tmp_path)
        ref = svc.locate(None, None, None, None, "ev-05")
        assert ref.document_name == "Unknown source"


# ---------------------------------------------------------------------------
# _locate_pdf (mocked pypdf)
# ---------------------------------------------------------------------------
class TestLocatePdf:
    def _make_pdf_bytes(self) -> bytes:
        return b"%PDF-1.4 test content revenue 5000 FY2023"

    def test_pdf_locates_page_with_matching_content(self, tmp_path):
        pdf_file = tmp_path / "report.pdf"
        pdf_file.write_bytes(self._make_pdf_bytes())

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "revenue 5000 FY2023 balance sheet"
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        svc = make_service(tmp_path)
        with patch("source_locator.PdfReader", return_value=mock_reader):
            loc, excerpt = svc._locate_pdf(pdf_file, "revenue", "FY2023", 5000)

        assert loc.type == "page"
        assert loc.page == 1
        assert excerpt is not None

    def test_non_pdf_bytes_returns_file_locator(self, tmp_path):
        bad_file = tmp_path / "fake.pdf"
        bad_file.write_bytes(b"NOT_A_PDF_HEADER")
        svc = make_service(tmp_path)
        loc, excerpt = svc._locate_pdf(bad_file, None, None, None)
        assert loc.type == "file"

    def test_pypdf_not_installed_returns_file_locator(self, tmp_path):
        pdf_file = tmp_path / "doc.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")
        svc = make_service(tmp_path)
        with patch.dict("sys.modules", {"pypdf": None}):
            # Force ImportError path
            with patch("builtins.__import__", side_effect=ImportError):
                loc, excerpt = svc._locate_pdf(pdf_file, None, None, None)
        assert loc.type == "file"


# ---------------------------------------------------------------------------
# _locate_docx (mocked python-docx)
# ---------------------------------------------------------------------------
class TestLocateDocx:
    def test_finds_matching_paragraph(self, tmp_path):
        docx_file = tmp_path / "report.docx"
        docx_file.write_bytes(b"placeholder")

        mock_para1 = MagicMock()
        mock_para1.text = "This contains revenue information"
        mock_para2 = MagicMock()
        mock_para2.text = "Unrelated content"
        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para1, mock_para2]

        svc = make_service(tmp_path)
        with patch("source_locator.Document", return_value=mock_doc):
            loc, excerpt = svc._locate_docx(docx_file, "revenue", None, None)

        assert loc.type == "paragraph"
        assert loc.paragraph_index == 0

    def test_no_match_returns_file_locator(self, tmp_path):
        docx_file = tmp_path / "report.docx"
        docx_file.write_bytes(b"placeholder")

        mock_para = MagicMock()
        mock_para.text = "something unrelated"
        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para]

        svc = make_service(tmp_path)
        with patch("source_locator.Document", return_value=mock_doc):
            loc, excerpt = svc._locate_docx(docx_file, "revenue_from_operations", None, 99999)

        assert loc.type == "file"


# ---------------------------------------------------------------------------
# _locate_csv
# ---------------------------------------------------------------------------
class TestLocateCsv:
    def _write_csv(self, tmp_path: Path, rows: list) -> Path:
        csv_file = tmp_path / "data.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerows(rows)
        return csv_file

    def test_finds_matching_row_by_value(self, tmp_path):
        csv_file = self._write_csv(tmp_path, [
            ["Item", "FY2023", "FY2022"],
            ["revenue", "5000", "4000"],
            ["ebitda", "800", "600"],
        ])
        svc = make_service(tmp_path)
        loc, excerpt = svc._locate_csv(csv_file, "revenue", None, "5000")
        assert loc.type in ("sheet_cell", "sheet_row")
        assert loc.row_number == 2

    def test_finds_matching_row_by_field_name(self, tmp_path):
        csv_file = self._write_csv(tmp_path, [
            ["Field", "Value"],
            ["net_profit", "400"],
        ])
        svc = make_service(tmp_path)
        loc, excerpt = svc._locate_csv(csv_file, "net_profit", None, None)
        assert loc.row_number == 2

    def test_no_match_returns_file_locator(self, tmp_path):
        csv_file = self._write_csv(tmp_path, [["a", "b"], ["c", "d"]])
        svc = make_service(tmp_path)
        loc, excerpt = svc._locate_csv(csv_file, "revenue", None, "9999999")
        assert loc.type == "file"

    def test_before_after_context(self, tmp_path):
        csv_file = self._write_csv(tmp_path, [
            ["row1"],
            ["revenue", "5000"],
            ["row3"],
        ])
        svc = make_service(tmp_path)
        loc, _ = svc._locate_csv(csv_file, "revenue", None, "5000")
        assert loc.before_row is not None
        assert loc.after_row is not None


# ---------------------------------------------------------------------------
# _sheet_excerpt
# ---------------------------------------------------------------------------
class TestSheetExcerpt:
    def test_includes_sheet_name_and_row(self):
        row_map = {"A": "revenue", "B": "5000"}
        excerpt = SourceLocatorService._sheet_excerpt("Sheet1", 3, row_map, "revenue")
        assert "Sheet1" in excerpt
        assert "3" in excerpt

    def test_without_source_field(self):
        row_map = {"A": "data"}
        excerpt = SourceLocatorService._sheet_excerpt("Sheet1", 1, row_map, None)
        assert "Sheet1" in excerpt


# ---------------------------------------------------------------------------
# _excerpt
# ---------------------------------------------------------------------------
class TestExcerpt:
    def test_returns_context_around_needle(self):
        text = "This document contains revenue information from FY2023 and more data follows here"
        result = SourceLocatorService._excerpt(text, ["revenue"])
        assert "revenue" in result

    def test_falls_back_to_first_340_chars(self):
        text = "a" * 500
        result = SourceLocatorService._excerpt(text, ["notfound"])
        assert len(result) <= 340
