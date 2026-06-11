"""
gemini_writer.py
Gemini-powered content writer for CAM draft sections.

Model routing (cost vs quality):
  gemini-2.5-pro        $1.25/$10.00 per 1M  →  Executive Summary, Credit Recommendation
  gemini-2.5-flash      $0.30/$2.50  per 1M  →  Financial, Risk, Industry, Business Model,
                                                   Loan Structuring, Early Warning Signals
  gemini-2.5-flash-lite $0.10/$0.40  per 1M  →  Borrower Profile, Promoter, Group, Banking,
                                                   GST, Credit Bureau, Collateral, Legal,
                                                   Committee Notes, Annexures

Estimated cost per CAM:  $0.06 – $0.10
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# ── Model IDs ─────────────────────────────────────────────────────────────────
# Update these if Google releases newer stable versions.
MODEL_PRO        = "gemini-2.5-pro"
MODEL_FLASH      = "gemini-2.5-flash"
MODEL_FLASH_LITE = "gemini-2.5-flash-lite"

# ── Section → model routing ───────────────────────────────────────────────────
_SECTION_MODEL: Dict[str, str] = {
    # Critical decision sections
    "executive_summary":         MODEL_FLASH,
    "credit_recommendation":     MODEL_FLASH,

    # Analytical sections — good reasoning, moderate cost
    "financial_statement_analysis": MODEL_FLASH,
    "risk_assessment":              MODEL_FLASH,
    "industry_analysis":            MODEL_FLASH,
    "business_model_assessment":    MODEL_FLASH,
    "loan_structuring":             MODEL_FLASH,
    "early_warning_signals":        MODEL_FLASH,

    # Data-narrative sections — cheapest, fast
    "borrower_profile":          MODEL_FLASH_LITE,
    "promoter_profile":          MODEL_FLASH_LITE,
    "group_company_analysis":    MODEL_FLASH_LITE,
    "banking_analysis":          MODEL_FLASH_LITE,
    "gst_analysis":              MODEL_FLASH_LITE,
    "credit_bureau_analysis":    MODEL_FLASH_LITE,
    "collateral_analysis":       MODEL_FLASH_LITE,
    "legal_compliance_review":   MODEL_FLASH_LITE,
    "credit_committee_notes":    MODEL_FLASH_LITE,
    "annexures":                 MODEL_FLASH_LITE,
}

# ── System prompt (shared across all sections) ────────────────────────────────
_SYSTEM_PROMPT = """You are a senior credit analyst at a leading Indian commercial bank with 15 years of experience writing Credit Appraisal Memorandums (CAMs). Your writing is:
- Formal, analytical, and third-person ("the borrower", "the company", "the promoters")
- Grounded in Indian banking norms (RBI guidelines, IND AS accounting standards)
- Precise with numbers — always cite the actual figures provided in the data
- Honest about data gaps — if information is not available, state what is missing and recommend what the analyst should obtain
- Free of filler phrases like "it is important to note" or "it should be mentioned"

Write only plain paragraphs. Do not include section headers, bullet points, or markdown. Each paragraph should be 4–6 sentences. Use Indian number formatting context (crores, lakhs) where appropriate."""


# ── Per-section prompts ───────────────────────────────────────────────────────

def _prompt_executive_summary(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Executive Summary section of the CAM (target: 4–5 substantial paragraphs, approx. 1.5 pages).

Cover:
1. Borrower identity, nature of business, and loan request (amount, type, purpose)
2. Key financial highlights — revenue trajectory, profitability, leverage, liquidity
3. Banking behaviour and GST compliance assessment
4. Key risks identified and mitigating factors
5. Preliminary credit assessment and recommended disposition

Borrower data:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_borrower_profile(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Borrower Profile section of the CAM (target: 3–4 paragraphs, approx. 1.5 pages).

Cover:
1. Legal constitution, incorporation date, registered address, and operational history
2. Business activity, product/service lines, and primary markets served
3. Key registrations — CIN, GSTIN, PAN, and regulatory licenses
4. Ownership structure and any group linkages

Borrower overview data:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_promoter_profile(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Promoter Profile section of the CAM (target: 3–4 paragraphs, approx. 1.5 pages).

Cover:
1. Names, qualifications, and relevant industry experience of key promoters/directors
2. Track record and background of the management team
3. Any adverse flags — director risk scores, disqualifications, or related-party concerns
4. Succession planning and key-person dependency (if data available)

If specific promoter data is absent, clearly flag what due diligence documents should be obtained (DIN verification, CIBIL, KYC).

Promoter/director data:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_group_company_analysis(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Group Company Analysis section of the CAM (target: 2–3 paragraphs, approx. 1 page).

Cover:
1. Known group entities, sister concerns, and related parties
2. Inter-company transactions — loans, guarantees, trade payables/receivables
3. Any circular transactions, open charges, or related-party risk flags
4. Consolidated exposure and cross-default risk if applicable

Related-party and group data:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_industry_analysis(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Industry Analysis section of the CAM (target: 4–5 paragraphs, approx. 2 pages).

Cover:
1. Industry classification, sector overview, and current growth trajectory in India
2. Demand drivers, competitive landscape, and key players
3. Regulatory environment, government policies, and sector-specific risks
4. Macro factors — commodity prices, FX exposure, interest rate sensitivity
5. How the borrower's positioning compares to industry benchmarks

Industry and market data:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_business_model_assessment(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Business Model Assessment section of the CAM (target: 3–4 paragraphs, approx. 1.5 pages).

Cover:
1. Revenue model — how the company generates revenue, customer concentration, pricing power
2. Cost structure — fixed vs variable costs, operating leverage, working capital cycle
3. Financial trend trajectory — revenue growth, margin trends, improving/declining metrics
4. Operational strengths and vulnerabilities identified from financial data

Financial trends and business data:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_financial_statement_analysis(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Financial Statement Analysis section of the CAM (target: 8–10 paragraphs, approx. 5 pages). This is the most detailed section.

Structure as follows:
Paragraph 1–2: Balance Sheet Analysis — asset composition, fixed vs current assets, liability structure, net worth growth, leverage ratios
Paragraph 3–4: Profit & Loss Analysis — revenue growth rate (year-on-year), gross margins, EBITDA margins, PAT trends, cost efficiency
Paragraph 5–6: Cash Flow Analysis — operating cash flow quality, capex pattern, free cash flow, cash conversion cycle
Paragraph 7–8: Key Financial Ratios — current ratio, debt-to-equity, interest coverage, DSCR, return on equity/assets. Interpret each ratio against banking norms.
Paragraph 9–10: Observations and anomalies — any discrepancies, inconsistencies, or flags identified in the financials

Use actual figures from the data. Reference source documents and years. Identify trends across years.

Complete financial data:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_banking_analysis(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Banking Analysis section of the CAM (target: 4–5 paragraphs, approx. 2 pages).

Cover:
1. Banking relationship — name of banks, account type, and tenure
2. Average balances, monthly inflows and outflows, and utilisation patterns
3. Cheque bounce history, cash deposit concentration, and unusual transaction patterns
4. Overall banking behaviour score and what it indicates about the borrower's financial discipline
5. Any red flags — EMI defaults, irregular credits, sudden large transactions

Banking behaviour data:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_gst_analysis(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the GST Analysis section of the CAM (target: 3–4 paragraphs, approx. 1.5 pages).

Cover:
1. GSTIN status, filing regularity, and compliance record
2. GST-reported sales vs financials-reported revenue — compute and explain the discrepancy percentage
3. What the discrepancy (if any) indicates — under-reporting risk, seasonal variations, or data gaps
4. Authenticity score and overall GST risk assessment

If GST data is unavailable, write a proper due-diligence note on what should be obtained (GSTR-1, GSTR-3B, GST portal verification).

GST analytics data:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_credit_bureau_analysis(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Credit Bureau Analysis section of the CAM (target: 3–4 paragraphs, approx. 1.5 pages).

The upstream pipeline does not yet provide structured bureau data. Write a professional placeholder that:
1. Lists what bureau reports are required — CIBIL for individual promoters, CIBIL CMR or Experian for the company
2. Specifies the minimum acceptable CIBIL score thresholds as per standard banking norms
3. Notes what adverse flags would be disqualifying — DPD history, write-offs, settlements, wilful default classification
4. Recommends this section be completed by the RM before credit committee presentation

Context:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_collateral_analysis(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Collateral Analysis section of the CAM (target: 3–4 paragraphs, approx. 1.5 pages).

The upstream pipeline does not yet provide structured collateral data. Write a professional placeholder that:
1. Lists standard collateral types for this loan category — primary security and collateral security
2. Specifies what valuation and legal reports are required — approved valuer certificate, title search, encumbrance certificate, search report
3. Notes LTV (Loan-to-Value) norms applicable under RBI guidelines for this type of credit
4. Recommends specific documents the RM must collect before credit committee presentation

Context (loan type and amount if available):
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_legal_compliance(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Legal & Compliance Review section of the CAM (target: 3–4 paragraphs, approx. 1.5 pages).

Cover:
1. Tax compliance status — income tax filings verified, outstanding demands, status of PAN and TAN
2. GST compliance — regular filer status, any show-cause notices or demands
3. Statutory compliance — ROC filings, company law compliance, any ongoing litigation
4. Any adverse legal matters, court cases, or regulatory actions flagged

Legal and compliance data:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_risk_assessment(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Risk Assessment section of the CAM (target: 4–5 paragraphs, approx. 2 pages).

Structure as:
Paragraph 1: Financial risk — leverage, liquidity, debt serviceability, and key ratio flags
Paragraph 2: Operational risk — business concentration, customer/supplier dependency, working capital risk
Paragraph 3: Market and industry risk — sector volatility, macro headwinds, competitive pressures
Paragraph 4: Management and governance risk — promoter risk, key-person dependency, related-party concerns
Paragraph 5: Overall risk rating with key mitigants and residual risks

All risk data:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_loan_structuring(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Loan Structuring section of the CAM (target: 4–5 paragraphs, approx. 2 pages).

Cover:
1. Proposed loan amount, tenor, interest rate structure, and repayment schedule
2. Debt service coverage analysis — projected DSCR, ability to service from operating cash flow
3. Proposed covenants — financial covenants (leverage ratio, DSCR floor), operational covenants
4. Disbursement conditions and end-use monitoring mechanism
5. Exit strategy and prepayment provisions

Where specific terms are not yet set, provide recommended structuring based on the financial profile.

Loan and financial data:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_early_warning_signals(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Early Warning Signals section of the CAM (target: 3–4 paragraphs, approx. 1.5 pages).

Cover:
1. Financial early warning signals — ratio deterioration, declining margins, rising leverage
2. Operational signals — revenue decline, working capital stress, unusual cash patterns
3. Behavioural signals — banking irregularities, cheque bounces, GST discrepancies
4. External signals — industry headwinds, regulatory changes, macro risks affecting this borrower
5. Monitoring triggers — what specific events should trigger an account review

All flags and anomalies from all agents:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_credit_recommendation(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Credit Recommendation section of the CAM (target: 4–5 paragraphs, approx. 2 pages).

Cover:
1. Clear recommendation — Approve / Approve with conditions / Decline — with a one-sentence rationale
2. Key supporting factors — the 3–5 strongest reasons supporting the recommendation
3. Key concerns and conditions — conditions that must be satisfied before/after sanction
4. Proposed sanction terms — amount, rate, tenor, primary and collateral security
5. Post-disbursement monitoring requirements

This must read as a definitive, evidence-based recommendation grounded in the analysis above.

Full borrower profile and analysis summary:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_committee_notes(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Credit Committee Notes section of the CAM (target: 2–3 paragraphs, approx. 1 page).

This section is for the credit committee to record deviations, exceptions, and final sanction details. Write a professional placeholder that:
1. Notes what items the committee should review and record — deviations from policy, exception approvals, covenant modifications
2. Provides a structured template for recording the committee's decision and conditions
3. Leaves clear space for the committee secretary to fill in the final approved terms

Context:
{json.dumps(ctx, indent=2, default=str)}"""


def _prompt_annexures(ctx: Dict) -> str:
    return f"""{_SYSTEM_PROMPT}

Write the Annexures section of the CAM (target: 2–3 paragraphs, approx. 1 page).

Cover:
1. List all source documents that were processed as part of this appraisal — with document type and period covered
2. List any documents that are outstanding and must be obtained before sanction
3. Note the data sources used for each analytical section (Agent 1 extraction, Agent 2 enrichment, Agent 3 analysis)

Source documents and metadata:
{json.dumps(ctx, indent=2, default=str)}"""


# ── Prompt dispatcher ─────────────────────────────────────────────────────────

_PROMPT_FN = {
    "executive_summary":            _prompt_executive_summary,
    "borrower_profile":             _prompt_borrower_profile,
    "promoter_profile":             _prompt_promoter_profile,
    "group_company_analysis":       _prompt_group_company_analysis,
    "industry_analysis":            _prompt_industry_analysis,
    "business_model_assessment":    _prompt_business_model_assessment,
    "financial_statement_analysis": _prompt_financial_statement_analysis,
    "banking_analysis":             _prompt_banking_analysis,
    "gst_analysis":                 _prompt_gst_analysis,
    "credit_bureau_analysis":       _prompt_credit_bureau_analysis,
    "collateral_analysis":          _prompt_collateral_analysis,
    "legal_compliance_review":      _prompt_legal_compliance,
    "risk_assessment":              _prompt_risk_assessment,
    "loan_structuring":             _prompt_loan_structuring,
    "early_warning_signals":        _prompt_early_warning_signals,
    "credit_recommendation":        _prompt_credit_recommendation,
    "credit_committee_notes":       _prompt_committee_notes,
    "annexures":                    _prompt_annexures,
}


# ── Context builders (only relevant data per section) ─────────────────────────

def build_context(
    company_name: str,
    transformation: Dict[str, Any],
    enrichment: Dict[str, Any],
    analysis: Dict[str, Any],
    tabs: Dict[str, Any],
    source_documents: List[str],
) -> Dict[str, Any]:
    """Full context assembled once; sliced per section below."""
    overview = (
        enrichment.get("enriched_tabs", {}).get("overview", {})
        or tabs.get("overview", {})
        or {}
    )
    return {
        "company_name":      company_name,
        "overview":          overview,
        "balance_sheet":     tabs.get("balance_sheet", []),
        "income_statement":  tabs.get("income_statement", []),
        "cash_flow":         tabs.get("cash_flow", []),
        "ratio_report":      analysis.get("ratio_report", {}),
        "banking_behaviour": analysis.get("banking_behaviour", {}),
        "gst_analytics":     analysis.get("gst_analytics", {}),
        "trend_report":      analysis.get("trend_report", {}),
        "industry_intelligence": analysis.get("industry_intelligence", {}),
        "market_risk":       analysis.get("market_risk", {}),
        "related_party":     analysis.get("related_party", {}),
        "cash_flow_projection": analysis.get("cash_flow_projection", {}),
        "tax_compliance":    analysis.get("tax_compliance", {}),
        "recommendation":    analysis.get("status", "partial"),
        "source_documents":  source_documents,
        "loan_amount":       (
            transformation.get("tab_data", {}).get("overview", {}).get("loan_amount")
            or overview.get("loan_amount")
        ),
        "loan_type": (
            transformation.get("tab_data", {}).get("overview", {}).get("loan_type")
            or overview.get("loan_type")
        ),
    }


def _slice(ctx: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    return {k: ctx[k] for k in keys if k in ctx}


_SECTION_CONTEXT_KEYS: Dict[str, List[str]] = {
    "executive_summary": [
        "company_name", "overview", "income_statement", "ratio_report",
        "banking_behaviour", "gst_analytics", "recommendation", "loan_amount", "loan_type",
    ],
    "borrower_profile": [
        "company_name", "overview", "source_documents",
    ],
    "promoter_profile": [
        "company_name", "overview", "related_party",
    ],
    "group_company_analysis": [
        "company_name", "related_party",
    ],
    "industry_analysis": [
        "company_name", "overview", "industry_intelligence", "market_risk",
    ],
    "business_model_assessment": [
        "company_name", "overview", "income_statement", "balance_sheet",
        "trend_report", "ratio_report",
    ],
    "financial_statement_analysis": [
        "company_name", "balance_sheet", "income_statement", "cash_flow",
        "ratio_report", "trend_report",
    ],
    "banking_analysis": [
        "company_name", "banking_behaviour",
    ],
    "gst_analysis": [
        "company_name", "gst_analytics", "income_statement",
    ],
    "credit_bureau_analysis": [
        "company_name", "overview", "loan_amount", "loan_type",
    ],
    "collateral_analysis": [
        "company_name", "loan_amount", "loan_type", "balance_sheet",
    ],
    "legal_compliance_review": [
        "company_name", "overview", "tax_compliance", "gst_analytics",
    ],
    "risk_assessment": [
        "company_name", "ratio_report", "banking_behaviour", "market_risk",
        "gst_analytics", "trend_report", "related_party",
    ],
    "loan_structuring": [
        "company_name", "loan_amount", "loan_type", "cash_flow_projection",
        "ratio_report", "income_statement", "cash_flow",
    ],
    "early_warning_signals": [
        "company_name", "ratio_report", "banking_behaviour", "gst_analytics",
        "trend_report", "market_risk",
    ],
    "credit_recommendation": [
        "company_name", "overview", "income_statement", "balance_sheet",
        "ratio_report", "banking_behaviour", "gst_analytics", "recommendation",
        "loan_amount", "loan_type", "cash_flow_projection",
    ],
    "credit_committee_notes": [
        "company_name", "loan_amount", "loan_type", "recommendation",
    ],
    "annexures": [
        "company_name", "source_documents",
    ],
}


# ── Core writer ───────────────────────────────────────────────────────────────

class GeminiWriter:
    def __init__(self, api_key: str):
        self._client = genai.Client(api_key=api_key)

    async def _call_model(self, model_name: str, prompt: str) -> str:
        """Single model call. Raises on failure."""
        response = await self._client.aio.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=4096,
            ),
        )
        return response.text.strip()

    async def _write_section(self, section_id: str, ctx: Dict[str, Any]) -> Optional[str]:
        """Write one section with automatic fallback chain. Returns None only if all models fail."""
        prompt_fn = _PROMPT_FN.get(section_id)
        if not prompt_fn:
            return None

        primary_model = _SECTION_MODEL.get(section_id, MODEL_FLASH_LITE)
        context_slice = _slice(ctx, _SECTION_CONTEXT_KEYS.get(section_id, list(ctx.keys())))
        prompt = prompt_fn(context_slice)

        # Build fallback chain: if primary fails, try Flash, then Flash-Lite
        fallback_chain: List[str] = [primary_model]
        if primary_model == MODEL_PRO:
            fallback_chain.append(MODEL_FLASH)
        if MODEL_FLASH_LITE not in fallback_chain:
            fallback_chain.append(MODEL_FLASH_LITE)

        for model_name in fallback_chain:
            try:
                text = await self._call_model(model_name, prompt)
                if text:
                    if model_name != primary_model:
                        logger.info(
                            "gemini_writer: [%s] wrote %d chars via %s (fallback from %s)",
                            section_id, len(text), model_name, primary_model,
                        )
                    else:
                        logger.info(
                            "gemini_writer: [%s] wrote %d chars via %s",
                            section_id, len(text), model_name,
                        )
                    return text
            except Exception as exc:
                logger.warning(
                    "gemini_writer: [%s] %s failed (%s)%s",
                    section_id, model_name, exc,
                    " — trying fallback" if model_name != fallback_chain[-1] else " — keeping template text",
                )

        return None

    async def _write_all(self, section_ids: List[str], ctx: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """Run all section writes concurrently."""
        tasks = [self._write_section(sid, ctx) for sid in section_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            sid: (r if not isinstance(r, Exception) else None)
            for sid, r in zip(section_ids, results)
        }

    def enrich_draft_sections(
        self,
        section_ids: List[str],
        ctx: Dict[str, Any],
    ) -> Dict[str, Optional[str]]:
        """
        Synchronous entry point for the pipeline thread.
        Runs all 18 section rewrites concurrently and returns section_id → generated text.
        Sections that fail return None; the caller keeps the template text.
        """
        try:
            return asyncio.run(self._write_all(section_ids, ctx))
        except RuntimeError:
            # Already inside an event loop (e.g. during testing) — use a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self._write_all(section_ids, ctx))
                return future.result()
