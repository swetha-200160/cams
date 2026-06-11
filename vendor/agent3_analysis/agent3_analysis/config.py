"""
config.py
Central configuration for Agent 3 — Analysis Agent.
All LLM model names, thresholds, and constants live here.
"""

# ---------------------------------------------------------------------------
# Groq LLM
# ---------------------------------------------------------------------------
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_MAX_TOKENS = 2048
GROQ_TEMPERATURE = 0.0          # Deterministic for financial reasoning
GROQ_RETRY_ATTEMPTS = 3
GROQ_RETRY_DELAY_SECONDS = 2.0

# ---------------------------------------------------------------------------
# Financial ratio thresholds (industry-agnostic defaults)
# ---------------------------------------------------------------------------
DSCR_HEALTHY_MIN = 1.25         # Below this → flag as concern
CURRENT_RATIO_HEALTHY_MIN = 1.0
DEBT_EQUITY_HEALTHY_MAX = 3.0
EBITDA_MARGIN_HEALTHY_MIN = 0.10  # 10%

# ---------------------------------------------------------------------------
# Banking behaviour scoring weights
# ---------------------------------------------------------------------------
BANK_SCORE_WEIGHTS = {
    "inflow_stability": 0.30,
    "cheque_bounce_penalty": 0.25,
    "cash_deposit_risk": 0.20,
    "unusual_patterns": 0.25,
}

# ---------------------------------------------------------------------------
# GST analytics
# ---------------------------------------------------------------------------
GST_DISCREPANCY_THRESHOLD = 0.10    # >10% mismatch between GST sales and P&L revenue → flag

# ---------------------------------------------------------------------------
# Data sufficiency minimums
# ---------------------------------------------------------------------------
MIN_YEARS_FOR_TREND = 2             # Trend analysis requires at least 2 years of data
MIN_MONTHS_BANK_STATEMENT = 6       # Bank analysis requires at least 6 months

# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
API_HOST = "0.0.0.0"
API_PORT = 8001
API_TITLE = "CAMS — Agent 3: Analysis Agent"
API_VERSION = "1.0.0"
