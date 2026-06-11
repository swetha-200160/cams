# nodes/data_structuring.py
# ──────────────────────────────────────────────────────────────
# NODE 6 — Data Structuring
#
# Consumes state["extracted_tables"] in addition to cleaned_data.
#
# Why both sources matter:
#   cleaned_data (from Node 5) is the LLM's text-extracted interpretation.
#   It provides company identifiers (name, CIN, PAN) and normalized
#   figure arrays (revenue_figures, asset_figures, etc.).
#
#   extracted_tables (from Node 4) are the raw structured rows — exact
#   column headers and cell values as parsed by Docling/pandas.
#   They provide precise numeric values per year/column with no LLM
#   interpretation in between.
#
#   Giving the structuring LLM BOTH sources means:
#     - cleaned_data supplies identifiers and normalized field names
#     - extracted_tables supply precise numeric values per year/column
#   Together they map far more accurately to FinancialSchema than either alone.
#
# Two-step process:
#   Step 1: LLM fills FinancialSchema template from BOTH combined inputs
#   Step 2: Pydantic FinancialSchema validates the LLM output
#
# ── HALLUCINATION FIX ──────────────────────────────────────────
# Root cause: the original prompt said "Fill the JSON template" which
# instructed the LLM to complete ALL fields even when it had no data,
# causing it to invent plausible-looking values (e.g. gross_profit = 50%
# of revenue, ebitda estimated from pat, etc.).
#
# Two-layer fix:
#
#   Layer 1 — Prompt hardening:
#     - "ONLY use values EXPLICITLY present in the input data"
#     - "Do NOT calculate, estimate, derive or infer any value"
#     - "If a value is not directly stated, use null"
#     - doc_type injected per document — LLM knows a Bank Statement
#       should not have balance_sheet data
#     - Field mapping section tells LLM which input arrays → which schema
#
#   Layer 2 — Post-LLM source validation (_validate_against_source):
#     After the LLM responds, every numeric value in the structured
#     output is checked against the set of ALL known values built from
#     BOTH sources:
#       (a) cleaned_data figure arrays (Node 5 output)
#       (b) Node 4's financial_insights.extracted_values (cell-exact floats)
#     Any value not in the known set (within 1% tolerance) is set to null.
#     This is the final hard guard against hallucination.
#
# Fallback chain (unchanged):
#   LLM failure       → store cleaned_data as-is, log error
#   JSON parse failure → store cleaned_data as-is, log error
#   Pydantic failure  → store raw parsed dict (unvalidated), log error
# ──────────────────────────────────────────────────────────────

import json
from langchain.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

from tools.llm_client import get_chat_llm
from schemas.financial_schema import FinancialSchema
from state.agent_state import AgentState
from config import settings as _s

# ── Bank statement doc types — use transaction extraction prompt ──
BANK_DOC_TYPES = {
    "Bank Statement",
    "Credit Card Statement",
    "Overdraft / CC Account Statement",
}

# ── ITR doc types — use ITR extraction prompt ──────────────────
ITR_DOC_TYPES = {
    "Income Tax Return",
    "ITR-1 (Sahaj)",
    "ITR-2",
    "ITR-3",
    "ITR-4 (Sugam)",
    "ITR-5",
    "ITR-6",
    "ITR-7",
    "Form 26AS / AIS",
    "Form 16 (TDS Certificate)",
}

# ── GST doc types — use GST extraction prompt ──────────────────
GST_DOC_TYPES = {
    "GST Return",
    "GSTR-1 (Outward Supplies)",
    "GSTR-3B (Monthly Return)",
    "GSTR-9 (Annual Return)",
    "GSTR-9C (GST Audit)",
    "GSTR-2A (Auto-drafted Inward Supplies)",
    "GSTR-2B (Auto-drafted ITC Statement)",
}

# ── Character budgets — loaded from config.yaml ───────────────
MAX_CLEANED_CHARS = _s.STRUCTURING_MAX_CLEANED
MAX_TABLE_CHARS   = _s.STRUCTURING_MAX_TABLE

# ── Field lists and limits — loaded from config.yaml ─────────
BALANCE_SHEET_FIELDS      = _s.BALANCE_SHEET_FIELDS
INCOME_STMT_FIELDS        = _s.INCOME_STMT_FIELDS
CASH_FLOW_FIELDS          = _s.CASH_FLOW_FIELDS
RATIO_ANALYSIS_FIELDS     = _s.RATIO_ANALYSIS_FIELDS
DOC_TYPE_ALLOWED_SECTIONS = _s.DOC_TYPE_ALLOWED_SECTIONS
ALL_FINANCIAL_SECTIONS    = _s.ALL_FINANCIAL_SECTIONS
MAX_SANE_VALUE            = _s.MAX_SANE_VALUE
CLEANING_FIGURE_ARRAYS    = _s.CLEANING_FIGURE_ARRAYS

# ── Numeric fields per section — built from config.yaml values ─
# Maps each FinancialSchema section key to its numeric field list.
# Used by _validate_against_source() and _null_all_numeric_fields().
_NUMERIC_FIELDS = {
    "balance_sheets":      BALANCE_SHEET_FIELDS,
    "income_statements":   INCOME_STMT_FIELDS,
    "cash_flows":          CASH_FLOW_FIELDS,
    "ratio_analysis_data": RATIO_ANALYSIS_FIELDS,
}


# ── Structuring prompt ────────────────────────────────────────
# Merges dual-source structure (cleaned_data + tables) with
# anti-hallucination rules and doc_type context.
#
# Template variables: {doc_type}, {cleaned_data}, {tables}
STRUCTURE_PROMPT = PromptTemplate.from_template(
    """You are a financial data structuring expert for a bank.

DOCUMENT TYPE: {doc_type}

CRITICAL RULES — READ BEFORE FILLING:
1. ONLY use values that are EXPLICITLY present in the input data below.
2. Do NOT calculate, estimate, derive, or infer any value.
3. Do NOT use percentages of other values to guess missing fields.
4. If a value is NOT directly stated in the input data, use null.
5. A field with null is correct. A field with an invented number is WRONG.
6. Each year in the input becomes ONE object in the relevant list.
7. Only populate schema sections that match the document type:
   - Balance Sheet            → fill balance_sheets only
   - Income Statement         → fill income_statements only
   - Cash Flow Statement      → fill cash_flows only
   - Financial Statement /
     Annual Report            → fill all three sections if data exists
   - Bank Statement / GST /
     Income Tax / ROC         → fill identifiers only (no financial rows)

FIELD MAPPING — cleaned_data arrays → schema fields:
- net_sales_figures        → income_statements[].revenue_from_operations
- other_income_figures     → income_statements[].other_income
- material_cost_figures    → income_statements[].cost_of_material
- employee_cost_figures    → income_statements[].employee_benefit_expense
- finance_cost_figures     → income_statements[].finance_cost
- depreciation_figures     → income_statements[].depreciation
- share_capital_figures    → balance_sheets[].share_capital
- reserves_figures         → balance_sheets[].reserves_surplus
- lt_borrowing_figures     → balance_sheets[].long_term_borrowing
- st_borrowing_figures     → balance_sheets[].short_term_borrowing
- trade_payables_figures   → balance_sheets[].trade_payables
- fixed_assets_figures     → balance_sheets[].fixed_assets
- operating_cf_figures     → cash_flows[].operating_activities
- investing_cf_figures     → cash_flows[].investing_activities
- financing_cf_figures     → cash_flows[].financing_activities
- net_cash_figures         → cash_flows[].net_change_in_cash
RATIO ANALYSIS DATA field mapping (same year-based structure):
- net_sales_figures                  → ratio_analysis_data[].total_revenue_from_operations
- total_revenue_figures              → ratio_analysis_data[].total_revenue
- net_operating_income_figures       → ratio_analysis_data[].net_operating_income
- operating_expense_figures          → ratio_analysis_data[].operating_expense
- other_operating_expense_figures    → ratio_analysis_data[].other_operating_expense
- employee_cost_figures              → ratio_analysis_data[].employee_benefit_expense
- ebitda_figures                     → ratio_analysis_data[].ebitda
- profit_before_tax_figures          → ratio_analysis_data[].profit_before_tax
- current_tax_figures                → ratio_analysis_data[].current_tax
- deferred_tax_figures               → ratio_analysis_data[].deferred_tax
- net_income_figures                 → ratio_analysis_data[].net_income
- depreciation_figures               → ratio_analysis_data[].depreciation_amortization
- finance_cost_figures               → ratio_analysis_data[].finance_cost
- interest_payment_figures           → ratio_analysis_data[].interest_payment
- principal_repayment_figures        → ratio_analysis_data[].principal_repayment
- total_debt_service_figures         → ratio_analysis_data[].total_debt_service
- fixed_payment_obligation_figures   → ratio_analysis_data[].fixed_payment_obligation
- opening_debt_figures               → ratio_analysis_data[].opening_debt
- closing_debt_figures               → ratio_analysis_data[].closing_debt
- current_assets_figures             → ratio_analysis_data[].current_assets
- current_liabilities_figures        → ratio_analysis_data[].current_liabilities
- shareholder_equity_figures         → ratio_analysis_data[].shareholder_equity
- share_capital_figures              → ratio_analysis_data[].share_capital
- reserves_figures                   → ratio_analysis_data[].reserves_surplus
All other schema fields MUST remain null UNLESS the raw table rows explicitly contain them.

SOURCES TO USE:
- Use the RAW TABLE ROWS for precise numeric values per year/column.
  Table headers are the field names, rows contain the actual numbers.
- Use the CLEANED EXTRACTED DATA for company identifiers (name, CIN, PAN, industry)
  and as a cross-reference for normalized field names.

JSON template to fill (replace null with actual values ONLY if found in input):
{{
  "company_name": null,
  "industry": null,
  "cin": null,
  "pan": null,
  "balance_sheets": [
    {{
      "year": null,
      "share_capital": null,
      "reserves_surplus": null,
      "long_term_borrowing": null,
      "short_term_borrowing": null,
      "trade_payables": null,
      "fixed_assets": null
    }}
  ],
  "income_statements": [
    {{
      "year": null,
      "revenue_from_operations": null,
      "other_income": null,
      "cost_of_material": null,
      "employee_benefit_expense": null,
      "finance_cost": null,
      "depreciation": null
    }}
  ],
  "cash_flows": [
    {{
      "year": null,
      "operating_activities": null,
      "investing_activities": null,
      "financing_activities": null,
      "net_change_in_cash": null
    }}
  ],
  "ratio_analysis_data": [
    {{
      "year": null,
      "total_revenue_from_operations": null,
      "total_revenue": null,
      "net_operating_income": null,
      "operating_expense": null,
      "other_operating_expense": null,
      "employee_benefit_expense": null,
      "ebitda": null,
      "profit_before_tax": null,
      "current_tax": null,
      "deferred_tax": null,
      "net_income": null,
      "depreciation_amortization": null,
      "finance_cost": null,
      "interest_payment": null,
      "principal_repayment": null,
      "total_debt_service": null,
      "fixed_payment_obligation": null,
      "opening_debt": null,
      "closing_debt": null,
      "current_assets": null,
      "current_liabilities": null,
      "shareholder_equity": null,
      "share_capital": null,
      "reserves_surplus": null
    }}
  ],
  "raw_notes": null
}}

--- CLEANED EXTRACTED DATA (identifiers + normalized field names) ---
{cleaned_data}

--- RAW TABLE ROWS (precise numeric values per year — prefer these for numbers) ---
{tables}

Return ONLY the filled JSON. No explanation. No code fences."""
)


# ── Bank Statement extraction prompt ─────────────────────────
# Separate prompt for bank document types. Extracts transaction-level
# rows instead of financial statement fields.
# Template variables: {cleaned_data}, {tables}
BANK_STATEMENT_PROMPT = PromptTemplate.from_template(
    """You are a bank statement parser for a credit analysis system.

Extract ALL transactions from the bank statement data below.

For EACH transaction row return an object with these fields:
  date        - transaction date as YYYY-MM-DD (use null if not found)
  amount      - transaction amount as a positive number (null if missing)
  type        - "credit" if money came IN, "debit" if money went OUT
  balance     - running account balance after the transaction (null if missing)
  description - narration or description text (null if missing)
  mode        - one of: "cash", "cheque", "neft", "rtgs", "upi", "imps", "online", "other"
                Infer from description keywords (e.g. NEFT→neft, UPI→upi, CHQ/CHEQUE→cheque, CASH→cash)
  category    - one of: "business_income", "salary", "loan_repayment", "vendor_payment",
                "tax_payment", "cash_withdrawal", "bank_charges", "interest", "other"
                Infer from description (e.g. salary/payroll→salary, EMI/loan→loan_repayment)
  is_cash     - true if mode is "cash", else false
  is_cheque   - true if mode is "cheque", else false
  is_bounce   - true if description contains "bounce", "return", "dishonour", "RTN", "ECS RTN", else false

RULES:
1. Extract every transaction row — do not skip any.
2. Use null for any field you cannot determine. Do NOT guess amounts.
3. amount must always be positive regardless of debit/credit direction.
4. Return ONLY valid JSON — no explanation, no code fences.

JSON template:
{{
  "bank_transactions": [
    {{
      "date": null,
      "amount": null,
      "type": null,
      "balance": null,
      "description": null,
      "mode": null,
      "category": null,
      "is_cash": null,
      "is_cheque": null,
      "is_bounce": null
    }}
  ]
}}

--- CLEANED EXTRACTED DATA ---
{cleaned_data}

--- RAW TABLE ROWS (use these for transaction rows) ---
{tables}

Return ONLY the filled JSON."""
)


# ── GST return extraction prompt ──────────────────────────────
# Extracts structured GST data from GSTR-1, GSTR-3B, GSTR-9 documents.
# Template variables: {doc_type}, {cleaned_data}, {tables}
GST_EXTRACTION_PROMPT = PromptTemplate.from_template(
    """You are a GST return data extractor for a credit analysis system.

DOCUMENT TYPE: {doc_type}

Extract all available GST data from the document below.

Field guide:
  period               - Financial year (e.g. "FY25-26", "FY2025-26"). null if not found.
  gst_source           - Document type shorthand: use "{doc_type}"
  monthly_taxable_value - Array of monthly taxable turnover (mainly from GSTR-1 or GSTR-3B).
                         Each entry: {{"month": "Apr", "value": 380000000}}
                         Months: Apr, May, Jun, Jul, Aug, Sep, Oct, Nov, Dec, Jan, Feb, Mar
                         Use null array [] if monthly data is not available.
  annual_taxable_value  - Total annual taxable turnover. null if not found.
  igst                 - Integrated GST paid. null if not found. (mainly from GSTR-3B)
  cgst                 - Central GST paid. null if not found. (mainly from GSTR-3B)
  sgst                 - State GST paid. null if not found. (mainly from GSTR-3B)
  total_tax_paid       - Total GST paid (IGST + CGST + SGST). null if not found.
  gstr1_total_sales    - Total outward taxable supplies from GSTR-1. null if not found.
  gstr3b_total_sales   - Total outward taxable supplies from GSTR-3B. null if not found.
  b2b_sales            - Business-to-business sales. null if not found.
  export_sales         - Export sales (zero-rated). null if not found.
  domestic_sales       - Domestic sales total. null if not found.

RULES:
1. ONLY use values explicitly present in the data. Do NOT calculate or estimate.
2. Use null for any field not found in this document.
3. Amounts must be plain numbers (no commas, no currency symbols).
4. Return ONLY valid JSON — no explanation, no code fences.

JSON template:
{{
  "gst_data": {{
    "period": null,
    "gst_source": "{doc_type}",
    "monthly_taxable_value": [],
    "annual_taxable_value": null,
    "igst": null,
    "cgst": null,
    "sgst": null,
    "total_tax_paid": null,
    "gstr1_total_sales": null,
    "gstr3b_total_sales": null,
    "b2b_sales": null,
    "export_sales": null,
    "domestic_sales": null
  }}
}}

--- CLEANED EXTRACTED DATA ---
{cleaned_data}

--- RAW TABLE ROWS ---
{tables}

Return ONLY the filled JSON."""
)


# ── ITR extraction prompt ─────────────────────────────────────
# Extracts structured ITR data from Income Tax Return documents.
# Template variables: {doc_type}, {cleaned_data}, {tables}
ITR_EXTRACTION_PROMPT = PromptTemplate.from_template(
    """You are an Income Tax Return data extractor for a credit analysis system.

DOCUMENT TYPE: {doc_type}

Extract all available ITR data from the document below.

Field guide:
  period               - Financial year (e.g. "FY2025-26") or assessment year (e.g. "AY2025-26"). null if not found.
  itr_form             - ITR form number: "ITR-6", "ITR-3", "ITR-5", etc. null if not found.
  section              - Filing section: "139(1)" (on-time), "139(4)" (belated), "139(5)" (revised). null if not found.
  filed_on_time        - true if filed under section 139(1), false if belated/revised. null if not found.
  audit_applicable     - true if tax audit under section 44AB is applicable. null if not found.
  audit_completed      - true if audit report has been filed. null if not found.
  reported_total_income - Gross total income before deductions. null if not found.
  taxable_income       - Net taxable income after deductions (total income as per return). null if not found.
  total_receipts       - Total gross receipts on which TDS was deducted. null if not found.
  tds_deducted         - Total TDS deducted at source. null if not found.
  total_liability      - Total tax liability computed. null if not found.
  tds_credit           - TDS credit claimed. null if not found.
  advance_tax          - Advance tax paid. null if not found.
  net_payable          - Net tax payable after all credits (0 if fully paid). null if not found.

RULES:
1. ONLY use values explicitly present in the data. Do NOT calculate or estimate.
2. Use null for any field not found in this document.
3. Amounts must be plain numbers (no commas, no currency symbols).
4. Return ONLY valid JSON — no explanation, no code fences.

JSON template:
{{
  "itr_data": {{
    "period": null,
    "itr_form": null,
    "section": null,
    "filed_on_time": null,
    "audit_applicable": null,
    "audit_completed": null,
    "reported_total_income": null,
    "taxable_income": null,
    "total_receipts": null,
    "tds_deducted": null,
    "total_liability": null,
    "tds_credit": null,
    "advance_tax": null,
    "net_payable": null
  }}
}}

--- CLEANED EXTRACTED DATA ---
{cleaned_data}

--- RAW TABLE ROWS ---
{tables}

Return ONLY the filled JSON."""
)


def data_structuring_node(state: AgentState) -> AgentState:
    print("\n" + "─" * 55)
    print("🏗️   NODE 6 — Data Structuring")
    print("─" * 55)

    llm    = get_chat_llm()
    chain  = STRUCTURE_PROMPT | llm | StrOutputParser()
    errors = list(state.get("errors", []))
    structured = {}

    # ── Build doc_type lookup from classified_documents ───────
    # Injected into the prompt so the LLM knows which schema sections
    # to populate for this specific document type.
    doc_type_lookup = {
        doc["filename"]: doc.get("doc_type", "Unknown")
        for doc in state.get("classified_documents", [])
    }

    for filename, cleaned in state["cleaned_data"].items():

        # ── Skip empty inputs ─────────────────────────────────
        if not cleaned:
            print(f"   ⏭  {filename} — empty cleaned data, skipping")
            structured[filename] = {}
            continue

        doc_type   = doc_type_lookup.get(filename, "Unknown")

        # ── Pull raw table rows for this document (from Node 4) ──
        doc_tables = state.get("extracted_tables", {}).get(filename, [])

        # ── Serialize both inputs ─────────────────────────────
        try:
            cleaned_str = json.dumps(cleaned, ensure_ascii=False)[:MAX_CLEANED_CHARS]
        except Exception:
            cleaned_str = str(cleaned)[:MAX_CLEANED_CHARS]

        table_str = _build_table_context(doc_tables)

        if doc_tables:
            fin_tables = sum(
                1 for t in doc_tables
                if t.get("financial_insights", {}).get("is_financial_table")
            )
            print(
                f"   📊 {filename}  [{doc_type}] — "
                f"cleaned data + {len(doc_tables)} table(s) "
                f"({fin_tables} financial)"
            )
        else:
            print(f"   📝 {filename}  [{doc_type}] — cleaned data only (no tables)")

        # ── Branch: bank documents use transaction extraction ─
        if doc_type in BANK_DOC_TYPES:
            bank_chain = BANK_STATEMENT_PROMPT | llm | StrOutputParser()
            try:
                raw_response = bank_chain.invoke({
                    "cleaned_data": cleaned_str,
                    "tables":       table_str,
                })
                parsed_dict = _safe_parse_json(raw_response)
            except Exception as e:
                err = f"Bank extraction LLM call failed for {filename}: {e}"
                print(f"   ❌ {err}")
                errors.append(err)
                structured[filename] = {"bank_transactions": []}
                continue

            try:
                validated  = FinancialSchema(**parsed_dict)
                model_dict = validated.model_dump()
                txn_count  = len(model_dict.get("bank_transactions") or [])
                structured[filename] = model_dict
                print(f"   ✅ {filename}  [{doc_type}]  →  {txn_count} transaction(s) extracted")
            except Exception as e:
                err = f"Bank Pydantic validation failed for {filename}: {e}"
                print(f"   ⚠️  {filename}  →  Pydantic failed, storing raw parsed dict")
                errors.append(err)
                structured[filename] = parsed_dict
            continue

        # ── Branch: GST documents use GST extraction ──────────
        if doc_type in GST_DOC_TYPES:
            gst_chain = GST_EXTRACTION_PROMPT | llm | StrOutputParser()
            try:
                raw_response = gst_chain.invoke({
                    "doc_type":     doc_type,
                    "cleaned_data": cleaned_str,
                    "tables":       table_str,
                })
                parsed_dict = _safe_parse_json(raw_response)
            except Exception as e:
                err = f"GST extraction LLM call failed for {filename}: {e}"
                print(f"   ❌ {err}")
                errors.append(err)
                structured[filename] = {"gst_data": None}
                continue

            try:
                validated  = FinancialSchema(**parsed_dict)
                model_dict = validated.model_dump()
                gst        = model_dict.get("gst_data") or {}
                period     = gst.get("period") or "unknown"
                structured[filename] = model_dict
                print(f"   ✅ {filename}  [{doc_type}]  →  GST data extracted (period={period})")
            except Exception as e:
                err = f"GST Pydantic validation failed for {filename}: {e}"
                print(f"   ⚠️  {filename}  →  Pydantic failed, storing raw parsed dict")
                errors.append(err)
                structured[filename] = parsed_dict
            continue

        # ── Branch: ITR documents use ITR extraction ──────────
        if doc_type in ITR_DOC_TYPES:
            itr_chain = ITR_EXTRACTION_PROMPT | llm | StrOutputParser()
            try:
                raw_response = itr_chain.invoke({
                    "doc_type":     doc_type,
                    "cleaned_data": cleaned_str,
                    "tables":       table_str,
                })
                parsed_dict = _safe_parse_json(raw_response)
            except Exception as e:
                err = f"ITR extraction LLM call failed for {filename}: {e}"
                print(f"   ❌ {err}")
                errors.append(err)
                structured[filename] = {"itr_data": None}
                continue

            try:
                validated  = FinancialSchema(**parsed_dict)
                model_dict = validated.model_dump()
                itr        = model_dict.get("itr_data") or {}
                period     = itr.get("period") or "unknown"
                structured[filename] = model_dict
                print(f"   ✅ {filename}  [{doc_type}]  →  ITR data extracted (period={period})")
            except Exception as e:
                err = f"ITR Pydantic validation failed for {filename}: {e}"
                print(f"   ⚠️  {filename}  →  Pydantic failed, storing raw parsed dict")
                errors.append(err)
                structured[filename] = parsed_dict
            continue

        # ── LLM structuring call ──────────────────────────────
        try:
            raw_response = chain.invoke({
                "doc_type":     doc_type,
                "cleaned_data": cleaned_str,
                "tables":       table_str,
            })
            parsed_dict = _safe_parse_json(raw_response)

        except Exception as e:
            err = f"Structuring LLM call failed for {filename}: {e}"
            print(f"   ❌ {err}")
            errors.append(err)
            structured[filename] = cleaned
            continue

        # ── Layer 2: Source validation (anti-hallucination) ───
        # Null out any numeric value that does not appear in the
        # known values set built from BOTH cleaned_data AND Node 4
        # table insights. Runs BEFORE Pydantic.
        hallucination_count = _validate_against_source(
            parsed_dict, cleaned, doc_tables
        )
        if hallucination_count > 0:
            print(
                f"   🛡️  {filename}  →  {hallucination_count} hallucinated "
                f"value(s) removed before Pydantic validation"
            )

        # ── Pydantic validation ───────────────────────────────
        try:
            validated  = FinancialSchema(**parsed_dict)
            model_dict = validated.model_dump()

            # ── Section enforcement (post-Pydantic, code-level) ───
            # The prompt instructs the LLM to fill only sections that
            # match the doc_type, but the LLM can still populate wrong
            # sections when it finds relevant data. This enforcement runs
            # AFTER Pydantic and hard-nulls sections that cannot belong
            # to this document type — code-enforced, not prompt-dependent.
            sections_cleared = _enforce_doc_type_sections(model_dict, doc_type)
            if sections_cleared:
                print(
                    f"   🚫 {filename}  →  cleared {sections_cleared} "                    f"section(s) not valid for [{doc_type}]"
                )

            # Strip skeleton rows (all financial fields null) from every section.
            # model_dump() restores {all: None} template rows — remove them here.
            rows_stripped = _strip_empty_rows(model_dict)
            if rows_stripped:
                print(f"   🧹 {filename}  →  {rows_stripped} empty skeleton row(s) stripped")

            structured[filename] = model_dict
            print(f"   ✅ {filename}  [{doc_type}]  →  Pydantic validated OK")
            _log_structured_summary(validated)

        except Exception as e:
            err = f"Pydantic validation failed for {filename}: {e}"
            print(f"   ⚠️  {filename}  →  Pydantic failed, storing raw parsed dict")
            errors.append(err)
            structured[filename] = parsed_dict

    return {
        **state,
        "structured_datasets": structured,
        "errors": errors,
        "current_step": "data_structuring",
    }




# ══════════════════════════════════════════════════════════════
# EMPTY ROW STRIPPING
# ══════════════════════════════════════════════════════════════

def _strip_empty_rows(model_dict: dict) -> int:
    """
    Remove skeleton rows from all financial sections.

    Problem: Pydantic's model_dump() preserves rows where every financial
    field is None (skeleton rows). These look like:
        {"year": null, "total_assets": null, "equity": null, ...}
    They add noise to the output and pollute tab_mapping consolidation.

    A row is considered empty if ALL fields in its section-specific
    financial field list are None. The year field alone does not count.

    Mutates model_dict in place.

    Returns:
        Count of rows stripped across all sections.
    """
    stripped = 0
    for section, fin_fields in _NUMERIC_FIELDS.items():
        rows = model_dict.get(section)
        if not rows or not isinstance(rows, list):
            continue
        kept = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            # Keep only rows that have at least one non-null financial field
            if any(row.get(f) is not None for f in fin_fields):
                kept.append(row)
            else:
                stripped += 1
        model_dict[section] = kept
    return stripped


# ══════════════════════════════════════════════════════════════
# DOC-TYPE SECTION ENFORCEMENT
# ══════════════════════════════════════════════════════════════
# Maps doc_type to which FinancialSchema sections are ALLOWED to contain rows.
# Sections NOT in the allowed set are cleared to empty lists.
# This is a CODE-LEVEL guard that runs after Pydantic, regardless of what
# the LLM produced.
#
# A Balance Sheet document can NEVER contain valid income statement or
# cash flow data — that belongs to other documents. When the LLM fills
# those sections it causes duplicate rows in Node 7 consolidation.
#
# Sections allowed per doc_type:
#   Balance Sheet          -> balance_sheets only
#   Income Statement       -> income_statements only
#   Cash Flow Statement    -> cash_flows only
#   Financial Statement    -> all three (combined doc)
#   Annual Report          -> all three (contains all statements)
#   Bank / GST / ITR / ROC -> none (no financial statements)
#   Unknown                -> all three (cannot infer)

def _enforce_doc_type_sections(model_dict: dict, doc_type: str) -> int:
    """
    Hard-clear financial sections not valid for this doc_type.

    Runs on the model_dict (FinancialSchema.model_dump()) AFTER Pydantic.
    Replaces disallowed sections with empty lists, in-place.

    Args:
        model_dict : Validated schema dict — mutated in place.
        doc_type   : Document type string from Node 2 classification.

    Returns:
        Count of sections cleared (for logging).
    """
    allowed = DOC_TYPE_ALLOWED_SECTIONS.get(
        doc_type,
        ALL_FINANCIAL_SECTIONS
    )
    cleared = 0

    for section in ALL_FINANCIAL_SECTIONS:
        if section not in allowed:
            if model_dict.get(section):   # had rows — count as cleared
                cleared += 1
            model_dict[section] = []

    return cleared


# ══════════════════════════════════════════════════════════════
# TABLE CONTEXT BUILDER  (from uploaded file — unchanged)
# ══════════════════════════════════════════════════════════════

def _build_table_context(tables: list) -> str:
    """
    Serialize extracted table dicts into a readable text block for the LLM.

    Ordering: financial tables (is_financial_table=True from Node 4) are
    placed first so they occupy the limited character budget before any
    non-financial auxiliary tables.

    Row limits per table:
      Financial tables  → up to 30 rows (show full balance sheet, P&L,
                          cash flow line items)
      Non-financial     → up to 5 rows  (save budget for financial data)

    Total output is capped at MAX_TABLE_CHARS.
    """
    if not tables:
        return "No tables available."

    # Sort: financial tables first; within each group, preserve original order
    sorted_tables = sorted(
        tables,
        key=lambda t: 0 if t.get("financial_insights", {}).get("is_financial_table") else 1,
    )

    lines     = []
    table_num = 0

    for table in sorted_tables:
        insights  = table.get("financial_insights", {})
        is_fin    = insights.get("is_financial_table", False)
        headers   = table.get("headers", [])
        rows      = table.get("rows", [])
        source    = table.get("sheet", table.get("source", "unknown"))
        fin_type  = insights.get("financial_type", "")
        num_rows  = table.get("num_rows", len(rows))
        num_cols  = table.get("num_cols", len(headers))

        table_num  += 1
        table_label = f"{fin_type} / {source}" if fin_type and fin_type != "Unknown" else source
        lines.append(
            f"TABLE {table_num} [{table_label}] — "
            f"{num_rows} rows × {num_cols} cols"
            + (" [FINANCIAL]" if is_fin else "")
        )

        if headers:
            lines.append("Headers: " + " | ".join(str(h) for h in headers))

        row_limit = 30 if is_fin else 5
        for j, row in enumerate(rows[:row_limit]):
            if isinstance(row, dict):
                vals = " | ".join(
                    f"{k}: {v}"
                    for k, v in row.items()
                    if v is not None and str(v).strip() not in ("", "-", "N/A")
                )
            else:
                vals = str(row)
            lines.append(f"  Row {j + 1}: {vals}")

        if len(rows) > row_limit:
            lines.append(f"  ... ({len(rows) - row_limit} more rows truncated)")

        lines.append("")   # blank separator between tables

    result = "\n".join(lines)
    return result[:MAX_TABLE_CHARS]


# ══════════════════════════════════════════════════════════════
# ANTI-HALLUCINATION: SOURCE VALIDATION
# ══════════════════════════════════════════════════════════════

def _validate_against_source(
    parsed: dict, cleaned: dict, doc_tables: list
) -> int:
    """
    Remove hallucinated numeric values from the LLM-structured output.

    Builds the known-values set from TWO sources:
      (a) cleaned_data figure arrays (Node 5 output — already normalized floats)
      (b) Node 4's financial_insights.extracted_values (cell-exact parsed floats
          from _parse_numeric — highest accuracy, direct from table cells)

    Checks every numeric field in the LLM's parsed output. If a value is
    NOT in the known set (within 1% tolerance), it is set to null.

    Why two sources for known_values?
      - cleaned_data has the LLM-normalized figures from text extraction
      - Node 4 has cell-exact values from structured table parsing
      Both may cover different fields/years — unioning them gives the
      widest possible legitimate value set to validate against.

    Returns:
        Count of values that were nulled out (for logging).
    """
    known_values = _build_known_values(cleaned, doc_tables)

    if not known_values:
        # No numeric values in either source — can't validate anything.
        # Null ALL numeric fields to prevent the LLM from inventing the
        # entire financial profile from nothing.
        return _null_all_numeric_fields(parsed)

    nulled_count = 0

    for section_key, field_names in _NUMERIC_FIELDS.items():
        rows = parsed.get(section_key)
        if not rows or not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for field in field_names:
                value = row.get(field)
                if value is None:
                    continue
                if not isinstance(value, (int, float)):
                    continue
                # Hard reject corrupt / astronomical values (parser concatenation artefacts)
                if abs(float(value)) > MAX_SANE_VALUE:
                    row[field] = None
                    nulled_count += 1
                    continue
                if not _value_in_known(value, known_values):
                    row[field] = None
                    nulled_count += 1

    return nulled_count


def _build_known_values(cleaned: dict, doc_tables: list) -> set:
    """
    Build the set of ALL legitimate numeric values for this document.

    Source 1 — cleaned_data figure arrays (Node 5):
      revenue_figures, profit_figures, asset_figures, liability_figures,
      equity_figures, cash_flow_figures  →  {year, value} entries

    Source 2 — Node 4 financial_insights.extracted_values:
      financial_insights.extracted_values is a dict of
      { canonical_field: { canonical_year: float_value } }
      e.g. {"revenue": {"2023": 7400000.0}, "total_assets": {"2023": 15000000.0}}
      These are the most precise values — extracted directly from table cells
      by _parse_numeric() in table_detection.py.

    Zero values are excluded — they represent LLM placeholders, not real data.
    Values > MAX_SANE_VALUE are excluded — they are corrupt parse artefacts
    (e.g. multiple cell numbers accidentally concatenated by the parser).
    """
    known: set = set()

    # ── Source 1: cleaned_data figure arrays ──────────────────
    for array_name in CLEANING_FIGURE_ARRAYS:
        for entry in (cleaned.get(array_name) or []):
            if not isinstance(entry, dict):
                continue
            v = entry.get("value")
            if v is not None and isinstance(v, (int, float)) and v != 0.0:
                if abs(float(v)) <= MAX_SANE_VALUE:
                    known.add(float(v))

    # ── Source 2: Node 4 financial_insights.extracted_values ──
    for table in (doc_tables or []):
        insights = table.get("financial_insights", {})
        if not insights.get("is_financial_table"):
            continue
        extracted = insights.get("extracted_values", {})
        for field_key, year_value_map in extracted.items():
            if not isinstance(year_value_map, dict):
                continue
            for year, value in year_value_map.items():
                if value is not None and isinstance(value, (int, float)) and value != 0.0:
                    abs_val = abs(float(value))
                    if abs_val <= MAX_SANE_VALUE:
                        known.add(abs_val)  # abs: bracket negatives from Node 4

    return known


def _value_in_known(value: float, known_values: set) -> bool:
    """
    Return True if value is within 1% of any known value.
    Handles minor floating-point differences from LLM rounding.
    Also checks the absolute value — LLM may omit the negative sign
    on losses/outflows that Node 4 stored as negatives.
    """
    value = float(value)
    abs_value = abs(value)
    for known in known_values:
        if known == 0:
            continue
        if abs(abs_value - known) / known <= 0.01:
            return True
    return False


def _null_all_numeric_fields(parsed: dict) -> int:
    """
    When neither source has numeric values (e.g. a pure text document
    with no tables and no extractable figures), null out all numeric
    fields in the structured output to prevent the LLM from inventing
    the entire financial profile.
    """
    count = 0
    for section_key, field_names in _NUMERIC_FIELDS.items():
        for row in (parsed.get(section_key) or []):
            if not isinstance(row, dict):
                continue
            for field in field_names:
                if row.get(field) is not None:
                    row[field] = None
                    count += 1
    return count


# ══════════════════════════════════════════════════════════════
# SHARED HELPERS  (identical in both source files)
# ══════════════════════════════════════════════════════════════

def _safe_parse_json(raw: str) -> dict:
    """
    Parse JSON from LLM response, handling Llama 3.1 8B quirks:
      - Wrapped in ```json ... ``` fences
      - Leading/trailing whitespace
      - JSON object embedded in surrounding text
    """
    cleaned = raw.strip()
    cleaned = cleaned.lstrip("```json").lstrip("```").rstrip("```").strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end   = cleaned.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Could not parse JSON: {raw[:200]}")


def _log_structured_summary(schema: FinancialSchema):
    """Print a brief terminal summary after successful Pydantic validation."""
    lines = []
    if schema.company_name:
        lines.append(f"company={schema.company_name}")
    if schema.balance_sheets:
        lines.append(f"balance_sheets={len(schema.balance_sheets)}")
    if schema.income_statements:
        lines.append(f"income_statements={len(schema.income_statements)}")
    if schema.cash_flows:
        lines.append(f"cash_flows={len(schema.cash_flows)}")
    if schema.ratio_analysis_data:
        lines.append(f"ratio_analysis_data={len(schema.ratio_analysis_data)}")
    if lines:
        print(f"      → {' | '.join(lines)}")