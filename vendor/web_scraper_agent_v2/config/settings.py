"""
config/settings.py
Central configuration for the Web Scraper Agent.
All tuneable constants live here — never scatter magic values in business logic.
"""

from pathlib import Path

# ── Project paths ────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent
CACHE_DIR  = BASE_DIR / ".cache"
OUTPUT_DIR = BASE_DIR / "output"
LOG_DIR    = BASE_DIR / "logs"

# Input from Transformation Agent (real run vs dummy)
TRANSFORMATION_OUTPUT_FILE = BASE_DIR / "dummy_transformation_output.json"

# Output to Analysis Agent
ENRICHED_OUTPUT_FILE = OUTPUT_DIR / "enrich_output.json"

# ── Scraper: HTTP ────────────────────────────────────────────────────────────
# (requests per second, per domain)
RATE_LIMITS: dict[str, tuple[float, str]] = {
    "www.mca.gov.in":    (1.0, "second"),   # MCA21 — be conservative
    "mca.gov.in":        (1.0, "second"),
    "gst.gov.in":        (1.0, "second"),
    "services.gst.gov.in": (1.0, "second"),
    "ecourts.gov.in":    (2.0, "second"),
    "efiling.mca.gov.in":(1.0, "second"),
    "www.zaubacorp.com": (0.5, "second"),   # Zauba — stricter, public scrape
}

REQUEST_TIMEOUT_SECONDS = 30
RETRY_ATTEMPTS          = 3
RETRY_BACKOFF_BASE      = 2        # seconds; actual wait = base ** attempt

# Playwright (JS-heavy portals)
PLAYWRIGHT_HEADLESS     = True
PLAYWRIGHT_TIMEOUT_MS   = 30_000  # 30 s page load timeout

# ── Cache TTLs (seconds) ─────────────────────────────────────────────────────
CACHE_TTL: dict[str, int] = {
    "mca21":    7 * 24 * 3600,   # 7 days  — company master rarely changes
    "gst":      1 * 24 * 3600,   # 1 day   — filing status may update
    "ecourts":  3 * 24 * 3600,   # 3 days
    "zauba":    7 * 24 * 3600,   # 7 days
    "default":  1 * 24 * 3600,
}

# ── Validation thresholds ────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD   = 0.6    # below this → flagged for manual review
CROSS_VALIDATE_BOOST   = 0.3    # added when ≥ 2 sources agree
SINGLE_SOURCE_SCORE    = 0.6
CONFLICT_SCORE         = 0.4    # sources disagree → flag

# ── Fields that are ALWAYS flagged manual (no free source exists) ────────────
ALWAYS_MANUAL_FIELDS: set[str] = {
    "bank_statements",
    "cibil_report",
    "property_title_deeds",
    "valuation_report",
    "legal_opinion_report",
    "id_proof_directors",   # physical KYC document
}

# ── Source URLs ──────────────────────────────────────────────────────────────
URLS: dict[str, str] = {
    # MCA21 — public company master data (correct public endpoint, no auth)
    # efiling.mca.gov.in is INTERNAL — do not use it
    "mca21_master_data":   "https://www.mca.gov.in/MCAServices/rest/getCompanyDetails",
    "mca21_search_html":   "https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do",
    "mca21_charges":       "https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do",

    # Zauba — public MCA mirror (requires Playwright, httpx gets 403)
    "zauba_search":        "https://www.zaubacorp.com/company-search",

    # GST public GSTIN lookup
    "gst_search":          "https://services.gst.gov.in/services/api/search/search_by_gstin",

    # eCourts free REST API
    "ecourts_case_search": "https://services.ecourts.gov.in/ecourtindia_v6/",
}

# ── HTTP headers — full browser fingerprint to avoid 403s ───────────────────
# MCA21 and Zauba block requests that don't look like Chrome on Windows
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language":  "en-IN,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding":  "gzip, deflate, br",
    "Connection":       "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest":   "document",
    "Sec-Fetch-Mode":   "navigate",
    "Sec-Fetch-Site":   "none",
    "Sec-Fetch-User":   "?1",
    "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# ── Playwright — used for portals that 403 plain httpx (Zauba, MCA HTML) ────
USE_PLAYWRIGHT_FOR: set[str] = {
    "www.zaubacorp.com",
    "www.mca.gov.in",      # HTML fallback only — JSON API tried first
}

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL  = "INFO"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_FILE   = LOG_DIR / "web_scraper_agent.log"
