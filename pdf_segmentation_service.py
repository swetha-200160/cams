from __future__ import annotations

import io
import zipfile
from typing import List

from pypdf import PdfReader, PdfWriter


def split_pdf(pdf_bytes: bytes) -> List[bytes]:
    """Split a PDF into a list of single-page PDF byte buffers."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: List[bytes] = []
    for page in reader.pages:
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        pages.append(buf.getvalue())
    return pages


def build_zip(pages: List[bytes], stem: str) -> bytes:
    """Pack a list of single-page PDF buffers into an in-memory ZIP."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i, page_bytes in enumerate(pages, start=1):
            zf.writestr(f"{stem}_page_{i:03d}.pdf", page_bytes)
    return buf.getvalue()
