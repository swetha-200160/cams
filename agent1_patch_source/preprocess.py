from __future__ import annotations

from PIL import Image, ImageFilter, ImageOps


def deskew_and_enhance(img: Image.Image) -> Image.Image:
    """Lightweight preprocessing without extra native dependencies."""
    try:
        processed = img.convert("L")
        processed = ImageOps.autocontrast(processed)
        processed = processed.filter(ImageFilter.SHARPEN)
        return processed
    except Exception:
        return img
