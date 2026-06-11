# config/settings.py
#
# Single source of configuration for the entire Transformation Agent.
# All values are loaded from config/config.yaml at import time.
# No hardcoded constants below — edit config.yaml instead.

import yaml
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
with open(_CONFIG_PATH, encoding="utf-8") as _f:
    _cfg = yaml.safe_load(_f)

# ── LLM ───────────────────────────────────────────────────────
GROQ_MODEL       = _cfg["llm"]["model"]
MAX_PROMPT_CHARS = _cfg["llm"]["max_prompt_chars"]

# ── File Paths ────────────────────────────────────────────────
INPUT_FOLDER  = _cfg["paths"]["input_folder"]
OUTPUT_FOLDER = _cfg["paths"]["output_folder"]
OUTPUT_FILE   = _cfg["paths"]["output_file"]

# ── File Handling ─────────────────────────────────────────────
SUPPORTED_EXTENSIONS = _cfg["file_handling"]["supported_extensions"]
MIN_TABLE_ROWS       = _cfg["file_handling"]["min_table_rows"]
TABLE_TYPE_MIN_HITS  = _cfg["file_handling"]["table_type_min_hits"]

# ── Pipeline ──────────────────────────────────────────────────
TABS = _cfg["pipeline"]["tabs"]

# ── Character / Token Limits ──────────────────────────────────
CLEANING_MAX_TABLE_CHARS  = _cfg["limits"]["cleaning_max_table_chars"]
CLEANING_MAX_TEXT_CHARS   = _cfg["limits"]["cleaning_max_text_chars"]
CLEANING_HEADER_CHARS     = _cfg["limits"]["cleaning_header_chars"]
CLEANING_CF_CHARS         = _cfg["limits"]["cleaning_cf_chars"]
STRUCTURING_MAX_CLEANED   = _cfg["limits"]["structuring_max_cleaned_chars"]
STRUCTURING_MAX_TABLE     = _cfg["limits"]["structuring_max_table_chars"]
MAX_SANE_VALUE            = float(_cfg["limits"]["max_sane_value"])

# ── Financial Field Names per Tab ─────────────────────────────
BALANCE_SHEET_FIELDS    = _cfg["financial_fields"]["balance_sheet"]
INCOME_STMT_FIELDS      = _cfg["financial_fields"]["income_statement"]
CASH_FLOW_FIELDS        = _cfg["financial_fields"]["cash_flow"]
RATIO_ANALYSIS_FIELDS   = _cfg["financial_fields"]["ratio_analysis"]
META_FIELDS             = set(_cfg["financial_fields"]["meta_fields"])
ALL_FINANCIAL_SECTIONS  = set(_cfg["financial_fields"]["all_financial_sections"])

# ── Financial Field Synonyms ──────────────────────────────────
FINANCIAL_FIELDS = _cfg["financial_field_synonyms"]   # dict: field → [synonyms]

# ── Cleaning / Structuring Helpers ────────────────────────────
NODE4_TO_CLEANING_FIELD = _cfg["cleaning_field_map"]
NODE4_FIELD_PRIORITY    = _cfg["cleaning_field_priority"]
CLEANING_FIGURE_ARRAYS  = _cfg["cleaning_figure_arrays"]
CF_ANCHOR_PATTERNS      = _cfg["cash_flow_anchor_patterns"]

# ── Table Detection ───────────────────────────────────────────
TABLE_TYPE_SIGNATURES = _cfg["table_type_signatures"]
NULL_VALUE_STRINGS    = _cfg["null_value_strings"]

# ── Document Types ────────────────────────────────────────────
VALID_LABELS            = _cfg["document_types"]["valid_labels"]
EXTENSION_HINTS         = _cfg["document_types"]["extension_hints"]
NON_FINANCIAL_DOC_TYPES = set(_cfg["document_types"]["non_financial"])
DOC_TYPE_PRIORITY       = _cfg["document_types"]["priorities"]
RELIABLE_COMPANY_NAME_TYPES = _cfg["document_types"]["reliable_company_name_types"]
DOC_TYPE_ALLOWED_SECTIONS = {
    k: set(v)
    for k, v in _cfg["document_types"]["allowed_sections"].items()
}

# ── Document Classification ───────────────────────────────────
TEXT_KEYWORDS      = _cfg["text_keywords"]
KEYWORD_THRESHOLD  = _cfg["keyword_threshold"]
CATEGORY_TO_LABELS = _cfg["category_to_labels"]

# FILENAME_KEYWORD_MAP: convert [{keywords, label}] → [(keywords_list, label)] tuples
# so the consuming code's  `for keywords, label in FILENAME_KEYWORD_MAP`  works unchanged.
FILENAME_KEYWORD_MAP = [
    (entry["keywords"], entry["label"])
    for entry in _cfg["filename_keyword_map"]
]

# CATEGORY_RULES: convert [{label, subtypes: [{keywords, doc_type}]}]
# → [{label, subtypes: [(keywords_list, doc_type_str)]}] tuples
# so the consuming code's  `for keywords, doc_type in rule["subtypes"]`  works unchanged.
CATEGORY_RULES = [
    {
        "label": rule["label"],
        "subtypes": [
            (subtype["keywords"], subtype["doc_type"])
            for subtype in rule["subtypes"]
        ],
    }
    for rule in _cfg["category_rules"]
]

# ── Company Name Filtering ────────────────────────────────────
INVALID_COMPANY_NAMES = set(_cfg["company_name"]["invalid_names"])
NAME_STOP_WORDS       = set(_cfg["company_name"]["stop_words"])
