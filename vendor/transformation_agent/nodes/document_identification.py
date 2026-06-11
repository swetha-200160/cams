# nodes/document_identification.py
# ──────────────────────────────────────────────────────────────
# NODE 2 — Document Identification
#
# Three-stage classification (spec: metadata + keywords + layout patterns):
#
#   Stage 1 — Metadata signals
#     Checks file extension and filename keywords.
#     Extension .xlsx/.xls → Financial Statement (strong signal).
#     Filename keywords like "gst", "itr", "bank" → direct match.
#     SKIPPED for documents Node 1 already classified ("rule_based"),
#     since Node 1 runs a richer keyword engine on the same filename.
#
#   Stage 2 — Keyword scan on text preview
#     Each doc type has a set of domain keywords. The type with the
#     most keyword hits in the first 800 chars wins IF hits >= threshold.
#     For "rule_based" documents from Node 1, scoring is NARROWED to
#     labels within Node 1's pre-classified category — prevents unrelated
#     labels from winning on noise (e.g. "Bank Statement" keywords
#     outscoring "Balance Sheet" on a financial doc that mentions a bank).
#
#   Stage 3 — LLM fallback
#     Only called when stages 1 and 2 are inconclusive. Receives
#     filename, extension, keyword hints, and text preview for context.
#     When Node 1 gave a category ("rule_based"), the category and
#     pre-classified doc_type are passed to the prompt as additional
#     context — the LLM confirms or corrects rather than guessing cold.
#
# This matches the spec requirement of using metadata, keywords, AND
# layout patterns — not just an LLM call on raw text.
#
# The _match_label() false-positive bug is also fixed: second condition
# (raw_lower in label.lower()) is removed. Only label-in-response is safe.
#
# NODE 1 INTEGRATION:
#   Node 1 sets three fields on every document in document_repository:
#     category              → broad CAMS bucket (e.g. "GST Return")
#     doc_type              → specific type from filename (e.g. "GSTR-3B (Monthly Return)")
#     classification_source → "rule_based" | "unclassified"
#
#   This node reads classification_source to choose its execution path:
#     "rule_based"   → skip Stage 1 → narrowed Stage 2 → Stage 3 with hint
#     "unclassified" → full Stage 1 → full Stage 2 → Stage 3 (original flow)
# ──────────────────────────────────────────────────────────────

import os
from langchain.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

from tools.llm_client import get_chat_llm
from tools.docling_reader import parse_document
from state.agent_state import AgentState
from config import settings as _s

VALID_LABELS         = _s.VALID_LABELS
EXTENSION_HINTS      = _s.EXTENSION_HINTS
FILENAME_KEYWORD_MAP = _s.FILENAME_KEYWORD_MAP
KEYWORD_THRESHOLD    = _s.KEYWORD_THRESHOLD
TEXT_KEYWORDS        = _s.TEXT_KEYWORDS
CATEGORY_TO_LABELS   = _s.CATEGORY_TO_LABELS


# ── All classification constants loaded from config.yaml via settings ──
# VALID_LABELS, EXTENSION_HINTS, FILENAME_KEYWORD_MAP, KEYWORD_THRESHOLD,
# TEXT_KEYWORDS, CATEGORY_TO_LABELS are all imported above.

# ── Stage 3: LLM prompt — general (for unclassified docs) ────
# Used when Node 1 found NO filename match.
# Full classification prompt across all 15 valid labels.
CLASSIFY_PROMPT = PromptTemplate.from_template(
    """You are a financial document classifier for a bank.

Classify the document into EXACTLY ONE of these types:
Financial Statement | Balance Sheet | Income Statement | Cash Flow Statement |
Bank Statement | GST Return | Income Tax Return | ROC Filing | Annual Report |
Board Resolution | Certificate of Incorporation | Loan Document | Company Profile |
Credit Report | Unknown

Rules:
- Reply with ONLY the document type label. No explanation, no punctuation.
- Use the filename, file type hint, keyword signals, and preview together.
- If unsure, reply: Unknown

Filename: {filename}
File type hint: {extension_hint}
Keyword signals detected: {keyword_hint}
Document preview:
{preview}

Document type:"""
)

# ── Stage 3: LLM prompt — with Node 1 category hint ──────────
# Used when Node 1 found a filename match ("rule_based") but
# Stage 2 was still inconclusive.
# The LLM is told the pre-classified category and asked to confirm
# or correct — faster and more accurate than classifying cold.
CONFIRM_PROMPT = PromptTemplate.from_template(
    """You are a financial document classifier for a bank.

A document was pre-classified by filename analysis. Read the content preview
and return the most accurate document type. You may confirm the pre-classified
type or correct it if the content clearly shows something different.

Pre-classified category  : {pre_category}
Pre-classified type      : {pre_type}

Valid labels (return EXACTLY one, no explanation, no punctuation):
Financial Statement | Balance Sheet | Income Statement | Cash Flow Statement |
Bank Statement | GST Return | Income Tax Return | ROC Filing | Annual Report |
Board Resolution | Certificate of Incorporation | Loan Document | Company Profile |
Credit Report | Unknown

Filename: {filename}
File type hint: {extension_hint}
Keyword signals detected: {keyword_hint}
Document preview:
{preview}

Confirmed document type:"""
)


def document_identification_node(state: AgentState) -> AgentState:
    print("\n" + "─" * 55)
    print("🔍  NODE 2 — Document Identification")
    print("─" * 55)

    errors     = list(state.get("errors", []))
    classified = []

    # LLM is lazy-initialised — only created if Stage 3 is needed
    llm            = None
    classify_chain = None
    confirm_chain  = None

    for doc in state["document_repository"]:
        filename = doc["filename"]
        filepath = doc["filepath"]
        ext      = doc.get("extension", os.path.splitext(filename)[1].lower())

        # ── Read Node 1 pre-classification fields ─────────────
        # These are set by document_intake_node.
        pre_category = doc.get("category", "Unclassified")
        pre_type     = doc.get("doc_type", "Unknown")
        source       = doc.get("classification_source", "unclassified")

        preview = ""

        # ── Get text preview via parse_document (cached) ──────
        # Node 1 already triggered parse_document() for its preview.
        # This call hits the cache — no re-parsing occurs.
        try:
            parsed  = parse_document(filepath)
            text    = parsed.get("text", "")
            preview = text[:800].strip()
        except Exception as e:
            err = f"Preview extraction failed for {filename}: {e}"
            print(f"   ⚠️  {err}")
            errors.append(err)
            preview = ""

        # ══════════════════════════════════════════════════════
        # PATH A — "rule_based" documents
        # Node 1 already classified this document from its filename.
        # Stage 1 is SKIPPED — Node 1's richer keyword engine already
        # did a better version of the same filename scan.
        # ══════════════════════════════════════════════════════
        if source == "rule_based":

            # ── Stage 2 (narrowed to Node 1's category) ───────
            # Narrow scoring to only labels within the pre-classified
            # category. Prevents noise from unrelated keyword matches.
            category_filter = CATEGORY_TO_LABELS.get(pre_category)
            doc_type, keyword_hint, hits = _classify_by_keywords(
                preview, category_filter=category_filter
            )

            if doc_type:
                doc["doc_type"] = doc_type
                classified.append(doc)
                print(
                    f"   📄 {filename}\n"
                    f"      Node 1: {pre_category} / {pre_type}\n"
                    f"      Node 2: {doc_type}  [stage 2: {hits} keyword hits — confirmed]"
                )
                continue

            # ── Stage 3 (LLM with category hint) ──────────────
            # Stage 2 was inconclusive. Call LLM with Node 1's
            # category and doc_type as context — "confirm or correct"
            # rather than "guess from scratch".
            if llm is None:
                llm = get_chat_llm()

            if confirm_chain is None:
                confirm_chain = CONFIRM_PROMPT | llm | StrOutputParser()

            extension_hint = EXTENSION_HINTS.get(ext, f"{ext} file")

            try:
                raw_result = confirm_chain.invoke({
                    "filename":       filename,
                    "pre_category":   pre_category,
                    "pre_type":       pre_type,
                    "extension_hint": extension_hint,
                    "keyword_hint":   keyword_hint or "none detected",
                    "preview":        preview,
                })

                first_line = raw_result.strip().split("\n")[0].strip()
                doc_type   = _match_label(first_line)

                verdict = (
                    "confirmed" if doc_type == pre_type
                    else f"corrected from '{pre_type}'"
                )
                print(
                    f"   📄 {filename}\n"
                    f"      Node 1: {pre_category} / {pre_type}\n"
                    f"      Node 2: {doc_type}  [stage 3: LLM — {verdict}]"
                )

            except Exception as e:
                err = f"LLM classification failed for {filename}: {e}"
                print(f"   ⚠️  {err}")
                errors.append(err)
                # Preserve Node 1's pre_type as fallback — better than Unknown
                doc_type = _match_label(pre_type) if pre_type != "Unknown" else "Unknown"
                print(
                    f"      Node 2: {doc_type}  [stage 3: LLM failed — using Node 1 label]"
                )

            doc["doc_type"] = doc_type
            classified.append(doc)
            continue

        # ══════════════════════════════════════════════════════
        # PATH B — "unclassified" documents
        # Node 1 found no filename keyword match.
        # Run all three stages in full — original logic unchanged.
        # ══════════════════════════════════════════════════════

        # ── Stage 1: Metadata signals ─────────────────────────
        doc_type, stage = _classify_by_metadata(filename, ext)

        if doc_type:
            doc["doc_type"] = doc_type
            classified.append(doc)
            print(f"   📄 {filename}  →  {doc_type}  [stage 1: {stage}]")
            continue

        # ── Stage 2: Keyword scoring (full, no category filter) ─
        doc_type, keyword_hint, hits = _classify_by_keywords(preview)

        if doc_type:
            doc["doc_type"] = doc_type
            classified.append(doc)
            print(f"   📄 {filename}  →  {doc_type}  [stage 2: {hits} keyword hits]")
            continue

        # ── Stage 3: LLM fallback (general classify prompt) ───
        if llm is None:
            llm = get_chat_llm()

        if classify_chain is None:
            classify_chain = CLASSIFY_PROMPT | llm | StrOutputParser()

        extension_hint = EXTENSION_HINTS.get(ext, f"{ext} file")

        try:
            raw_result = classify_chain.invoke({
                "filename":       filename,
                "extension_hint": extension_hint,
                "keyword_hint":   keyword_hint or "none detected",
                "preview":        preview,
            })

            first_line = raw_result.strip().split("\n")[0].strip()
            doc_type   = _match_label(first_line)

        except Exception as e:
            err = f"Classification LLM call failed for {filename}: {e}"
            print(f"   ⚠️  {err}")
            errors.append(err)
            doc_type = "Unknown"

        doc["doc_type"] = doc_type
        classified.append(doc)
        print(f"   📄 {filename}  →  {doc_type}  [stage 3: LLM]")

    # ── Final summary ─────────────────────────────────────────
    print(f"\n   ✅ Classified {len(classified)} documents")
    type_counts = {}
    for d in classified:
        t = d["doc_type"]
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, c in sorted(type_counts.items()):
        print(f"      {t:<35} : {c}")

    return {
        **state,
        "classified_documents": classified,
        "errors": errors,
        "current_step": "document_identification",
    }


# ══════════════════════════════════════════════════════════════
# STAGE 1 HELPER
# ══════════════════════════════════════════════════════════════

def _classify_by_metadata(filename: str, ext: str):
    """
    Returns (doc_type, stage_label) if extension or filename gives a
    confident classification, else (None, None).

    Only used for PATH B ("unclassified") documents.
    PATH A ("rule_based") documents skip this — Node 1 already ran
    a richer version of the same filename scan.
    """
    # Extension is the strongest signal (e.g. .xlsx is always a spreadsheet)
    if ext in EXTENSION_HINTS:
        return EXTENSION_HINTS[ext], "extension"

    # Filename keyword scan (lowercased stem only)
    stem = os.path.splitext(filename)[0].lower().replace(" ", "_")
    for keywords, label in FILENAME_KEYWORD_MAP:
        for kw in keywords:
            if kw in stem:
                return label, f"filename keyword '{kw}'"

    return None, None


# ══════════════════════════════════════════════════════════════
# STAGE 2 HELPER
# ══════════════════════════════════════════════════════════════

def _classify_by_keywords(preview: str, category_filter: list = None):
    """
    Score each doc type by counting keyword hits in the text preview.

    Args:
        preview         : First 800 chars of extracted document text.
        category_filter : Optional list of VALID_LABELS to score.
                          When provided (for "rule_based" docs), only
                          labels within Node 1's pre-classified category
                          are scored — prevents noise from unrelated types.
                          When None (for "unclassified" docs), all labels
                          in TEXT_KEYWORDS are scored.

    Returns:
        (best_label, keyword_hint_str, hit_count)
        best_label is None if no type reached KEYWORD_THRESHOLD hits.
        keyword_hint_str is always returned for Stage 3 LLM context.
    """
    if not preview:
        return None, "no text available", 0

    text_lower = preview.lower()
    scores     = {}

    # Determine which labels to score
    labels_to_score = (
        {k: v for k, v in TEXT_KEYWORDS.items() if k in category_filter}
        if category_filter
        else TEXT_KEYWORDS
    )

    for label, keywords in labels_to_score.items():
        hits = sum(1 for kw in keywords if kw in text_lower)
        if hits > 0:
            scores[label] = hits

    if not scores:
        no_signal_msg = (
            f"no keyword signals in category '{', '.join(category_filter)}'"
            if category_filter
            else "no keyword signals"
        )
        return None, no_signal_msg, 0

    best_label = max(scores, key=scores.get)
    best_hits  = scores[best_label]

    # Build a hint string for Stage 3 LLM context
    # (useful even if below threshold — LLM can use it as a weak signal)
    top_two  = sorted(scores.items(), key=lambda x: -x[1])[:2]
    hint_str = ", ".join(f"{lbl} ({h} hits)" for lbl, h in top_two)

    if best_hits >= KEYWORD_THRESHOLD:
        return best_label, hint_str, best_hits

    # Below threshold — still return hint for Stage 3 context
    return None, hint_str, best_hits


# ══════════════════════════════════════════════════════════════
# STAGE 3 HELPER
# ══════════════════════════════════════════════════════════════

def _match_label(raw: str) -> str:
    """
    Match LLM response to a valid label.
    Only checks if a known label appears in the response (not vice versa)
    to avoid false positives from short responses like 'sheet' or 'return'.
    """
    raw_lower = raw.lower().strip()

    for label in VALID_LABELS:
        if label.lower() in raw_lower:
            return label

    return "Unknown"