# tools/llm_client.py
# ──────────────────────────────────────────────────────────────
# Shared Groq LLM client.
# Import get_chat_llm() in any node that needs LLM inference.
# Using a factory function (not a module-level singleton) so each
# node gets a fresh instance — avoids state leakage between calls.
# ──────────────────────────────────────────────────────────────

import os
from langchain_groq import ChatGroq
from dotenv import load_dotenv

load_dotenv()


def get_chat_llm() -> ChatGroq:
    """
    Return a configured ChatGroq instance for Llama 3.1 8B.

    Config rationale:
      temperature=0   → deterministic output, critical for financial data
      max_tokens=2048 → was 1024, too small for full FinancialSchema JSON
      timeout=60      → Groq is fast but network latency can vary
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GROQ_API_KEY not found. Add it to your .env file.\n"
            "Get a free key at: https://console.groq.com"
        )

    return ChatGroq(
        model="llama-3.1-8b-instant",
        api_key=api_key,
        temperature=0,
        max_tokens=2048,    # was 1024 — too small for full FinancialSchema JSON
        timeout=60,
    )