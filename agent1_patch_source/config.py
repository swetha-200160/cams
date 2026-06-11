from __future__ import annotations

import os
from pydantic import BaseModel, Field


class Settings(BaseModel):
    groq_api_key: str = Field(default_factory=lambda: os.environ.get("GROQ_API_KEY", "").strip())
    groq_model: str = Field(default_factory=lambda: os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant").strip() or "llama-3.1-8b-instant")
    request_timeout_s: int = Field(default_factory=lambda: int(os.environ.get("GROQ_TIMEOUT_S", "60")))
    max_chars_to_llm: int = Field(default_factory=lambda: int(os.environ.get("AGENT1_MAX_CHARS_TO_LLM", "32000")))
    max_pages_pdf: int = Field(default_factory=lambda: int(os.environ.get("AGENT1_MAX_PDF_PAGES", "20")))
    ocr_lang: str = Field(default_factory=lambda: os.environ.get("AGENT1_OCR_LANG", "eng").strip() or "eng")


settings = Settings()
