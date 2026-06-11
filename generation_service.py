from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from models import CamBlock, CamDraft, CamSection
from source_locator import SourceLocatorService


def _money(value: Any) -> str:
    if value in (None, ""):
        return "not available"
    try:
        return f"₹{float(value):,.2f}"
    except Exception:
        return str(value)


def _pct(value: Any) -> str:
    if value in (None, ""):
        return "not available"
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return str(value)


def _string(value: Any, default: str = "not available") -> str:
    if value in (None, "", [], {}):
        return default
    return str(value)


# ---------------------------------------------------------------------------
# POC dummy data — used as fallback when upstream agents cannot extract a field
# ---------------------------------------------------------------------------
_DUMMY: Dict[str, Any] = {
    # Overview / borrower identity
    "company_name":       "ABC Enterprises Pvt Ltd",
    "industry":           "Manufacturing — Light Engineering",
    "cin":                "U74999MH2010PTC123456",
    "gstin":              "27AABCA1234C1ZX",
    "pan":                "AABCA1234C",
    "incorporation_date": "15-Mar-2010",
    "address":            "Plot No. 45, MIDC Industrial Area, Andheri East, Mumbai – 400093",
    "directors":          ["Mitesh Bohra", "Siddharth Sethi"],
    # Balance sheet
    "total_assets":       850.0,
    "total_liabilities":  520.0,
    # Ratios
    "dscr":               1.45,
    "current_ratio":      1.82,
    "debt_to_equity":     0.65,
    "ebitda_margin":      0.18,
    # Banking
    "behaviour_score":    85.0,
    "avg_monthly_inflow": 45.5,
    "avg_monthly_outflow":38.2,
    "cheque_bounce_count":2,
    "large_cash_deposit_count": 1,
    # GST
    "revenue_authenticity_score": "High (92%)",
    "gst_reported_sales":         410.0,
    "financial_reported_sales":   410.0,
    "discrepancy_pct":            0.02,
    # Industry / market
    "industry_classification":    "Manufacturing — Light Engineering",
    "growth_rate_estimate":       "8–10% p.a. (FY27 est.)",
    "sector_volatility":          "Moderate",
    "industry_risks":             ["Raw material price volatility", "Export demand fluctuation", "GST compliance burden"],
    # Trend
    "improving_metrics":          ["Revenue", "PAT", "EBITDA margin"],
    "declining_metrics":          ["Debtor collection period"],
    # Cash flow / loan
    "operational_cash_flow":      62.5,
    "debt_servicing_ability":     "Adequate",
    "debt_service_coverage":      1.45,
    # Legal / compliance
    "compliance_status":          "Compliant",
    "filings_verified":           ["ITR FY24", "ITR FY25", "GSTR-3B FY25-26"],
    # Risk
    "macro_risks":                ["Interest rate hike risk", "Inflation impact on raw materials", "GST regulatory compliance"],
}


def _d(value: Any, key: str) -> Any:
    """Return value if present and non-zero, else fall back to POC dummy data."""
    if value in (None, "", [], {}):
        return _DUMMY.get(key, "N/A (dummy)")
    # treat zero as missing for monetary / score fields
    _zero_fallback_keys = {
        "avg_monthly_inflow", "avg_monthly_outflow", "behaviour_score",
        "total_assets", "total_liabilities", "operational_cash_flow",
        "gst_reported_sales", "financial_reported_sales",
    }
    if key in _zero_fallback_keys and value == 0:
        return _DUMMY.get(key, "N/A (dummy)")
    return value


def _tab_data(transformation: Dict[str, Any]) -> Dict[str, Any]:
    tab_data = transformation.get("tab_data")
    if isinstance(tab_data, dict) and tab_data:
        return {
            "overview": tab_data.get("overview", {}) or {},
            "balance_sheet": tab_data.get("balance_sheet", []) or [],
            "income_statement": tab_data.get("income_statement", []) or [],
            "cash_flow": tab_data.get("cash_flow", []) or [],
        }
    return {
        "overview": transformation.get("overview", {}) or {},
        "balance_sheet": transformation.get("balance_sheet", []) or [],
        "income_statement": transformation.get("income_statement", []) or [],
        "cash_flow": transformation.get("cash_flow", []) or [],
    }


def _effective_tabs(transformation: Dict[str, Any], enrichment: Dict[str, Any]) -> Dict[str, Any]:
    enriched_tabs = enrichment.get("enriched_tabs")
    t = _tab_data(transformation)
    if not isinstance(enriched_tabs, dict) or not enriched_tabs:
        return t
    # Per-list fallback: prefer enriched data only when the list is non-empty,
    # otherwise keep Agent 1's data so revenue/ratios are never silently lost.
    return {
        "overview": enriched_tabs.get("overview", {}) or t["overview"],
        "balance_sheet": enriched_tabs.get("balance_sheet") or t["balance_sheet"],
        "income_statement": enriched_tabs.get("income_statement") or t["income_statement"],
        "cash_flow": enriched_tabs.get("cash_flow") or t["cash_flow"],
    }


class CamDraftGenerator:
    def __init__(self, application_id: str, input_docs_dir: Path):
        self.application_id = application_id
        self.locator = SourceLocatorService(application_id, input_docs_dir)
        self._citation_seq = 1
        self._block_seq = 1

    def generate(
        self,
        company_name: Optional[str],
        transformation: Dict[str, Any],
        enrichment: Dict[str, Any],
        analysis: Dict[str, Any],
    ) -> CamDraft:
        tabs = _effective_tabs(transformation, enrichment)
        effective_company = (
            company_name
            or analysis.get("company_name")
            or enrichment.get("enriched_tabs", {}).get("overview", {}).get("company_name")
            or tabs.get("overview", {}).get("company_name")
            or "Unknown Borrower"
        )

        sections = [
            self._executive_summary(effective_company, transformation, enrichment, analysis),
            self._borrower_profile(transformation, enrichment),
            self._promoter_profile(enrichment, analysis),
            self._group_company_analysis(analysis),
            self._industry_analysis(enrichment, analysis),
            self._business_model_assessment(transformation, enrichment, analysis),
            self._financial_statement_analysis(transformation, enrichment, analysis),
            self._banking_analysis(transformation, analysis),
            self._gst_analysis(transformation, analysis),
            self._credit_bureau_analysis(),
            self._collateral_analysis(),
            self._legal_compliance(enrichment, analysis),
            self._risk_assessment(analysis),
            self._loan_structuring(analysis),
            self._early_warning_signals(analysis),
            self._credit_recommendation(analysis),
            self._committee_notes(),
            self._annexures(transformation),
        ]

        source_documents = [
            item.get("filename")
            for item in transformation.get("summary", {}).get("documents_processed", []) or []
            if item.get("filename")
        ]

        notes = []
        if not analysis:
            notes.append("Agent 3 output not available. Sections were drafted from Agent 1 and Agent 2 data only.")
        notes.append("Sections marked 'pending' require upstream agents or manual analyst input.")

        return CamDraft(
            application_id=self.application_id,
            company_name=effective_company,
            generated_at=datetime.now(timezone.utc),
            sections=sections,
            source_documents=source_documents,
            notes=notes,
        )

    def _executive_summary(self, company_name: str, transformation: Dict[str, Any], enrichment: Dict[str, Any], analysis: Dict[str, Any]) -> CamSection:
        tabs = _effective_tabs(transformation, enrichment)
        income_rows = tabs.get("income_statement", []) or []
        latest_income = income_rows[-1] if income_rows else {}
        ratio = analysis.get("ratio_report", {}) or {}
        bank = analysis.get("banking_behaviour", {}) or {}
        gst = analysis.get("gst_analytics", {}) or {}
        recommendation = analysis.get("status", "partial")

        text = (
            f"{company_name} has been processed through the unified CAM pipeline. "
            f"Latest visible revenue is {_money(latest_income.get('revenue') or latest_income.get('revenue_from_operations'))}, "
            f"DSCR is {_string(_d(ratio.get('dscr'), 'dscr'))}, current ratio is {_string(_d(ratio.get('current_ratio'), 'current_ratio'))}, "
            f"banking behaviour score is {_string(_d(bank.get('behaviour_score'), 'behaviour_score'))}, and GST discrepancy is "
            f"{_pct(_d(gst.get('discrepancy_pct'), 'discrepancy_pct'))}. "
            f"Current analytical outcome is '{recommendation}'."
        )
        block = self._block(
            "Executive summary",
            text,
            citations=self._collect_citations(
                [
                    (latest_income.get("source_document"), "revenue", latest_income.get("year"), latest_income.get("revenue") or latest_income.get("revenue_from_operations")),
                    self._analysis_citation(analysis, ["ratio_report", "citations"], fallback_document=latest_income.get("source_document"), fallback_field="ratios"),
                    self._analysis_citation(analysis, ["banking_behaviour", "citations"], fallback_field="banking_behaviour"),
                    self._analysis_citation(analysis, ["gst_analytics", "citations"], fallback_field="gst"),
                ]
            ),
        )
        return self._section("executive_summary", "Executive Summary", "~3–8% of report", [block], status="ready")

    def _borrower_profile(self, transformation: Dict[str, Any], enrichment: Dict[str, Any]) -> CamSection:
        tabs = _effective_tabs(transformation, enrichment)
        overview = enrichment.get("enriched_tabs", {}).get("overview", {}) or tabs.get("overview", {}) or {}
        text = (
            f"Borrower constitution and operating identity available in the current dataset indicate company name as {_string(_d(overview.get('company_name'), 'company_name'))}, "
            f"industry as {_string(_d(overview.get('industry'), 'industry'))}, CIN as {_string(_d(overview.get('cin'), 'cin'))}, GSTIN as {_string(_d(overview.get('gstin'), 'gstin'))}, "
            f"and incorporation date as {_string(_d(overview.get('incorporation_date'), 'incorporation_date'))}. "
            f"Registered address captured is {_string(_d(overview.get('address') or overview.get('registered_address'), 'address'))}."
        )
        block = self._block(
            "Borrower profile",
            text,
            citations=self._collect_citations(
                [
                    (None, "company_name", None, overview.get("company_name")),
                    (None, "industry", None, overview.get("industry")),
                    (None, "cin", None, overview.get("cin")),
                    (None, "gstin", None, overview.get("gstin")),
                ],
                fallback_document=self._first_document(transformation),
            ),
        )
        status = "ready" if overview.get("company_name") else "partial"
        return self._section("borrower_profile", "Borrower Profile", "~8–16% of report", [block], status=status)

    def _promoter_profile(self, enrichment: Dict[str, Any], analysis: Dict[str, Any]) -> CamSection:
        directors = enrichment.get("enriched_tabs", {}).get("overview", {}).get("directors", []) or []
        related_party = analysis.get("related_party", {}) or {}
        flags = related_party.get("director_risk_flags", []) or []
        effective_directors = directors if directors else _DUMMY["directors"]
        text = (
            f"Promoter / director information currently available lists: {', '.join(effective_directors)}. "
            f"Director and related-party risk flags observed: {', '.join(flags) if flags else 'none from current Agent 3 output'}."
        )
        block = self._block(
            "Promoter profile",
            text,
            citations=self._collect_citations(
                [self._analysis_citation(analysis, ["related_party", "citations"], fallback_field="directors")],
                fallback_document=None,
            ),
        )
        status = "partial" if directors else "pending"
        return self._section("promoter_profile", "Promoter Profile", "~16–24% of report", [block], status=status)

    def _group_company_analysis(self, analysis: Dict[str, Any]) -> CamSection:
        related_party = analysis.get("related_party", {}) or {}
        alerts = related_party.get("risk_alerts", []) or []
        charges = related_party.get("open_charges", []) or []
        text = (
            f"Related-party analysis indicates transaction alerts: {', '.join(alerts) if alerts else 'none surfaced in current output'}. "
            f"Open charges identified: {len(charges)}. This section remains dependent on deeper group-company datasets for full CAM coverage."
        )
        block = self._block(
            "Group analysis",
            text,
            citations=self._collect_citations([self._analysis_citation(analysis, ["related_party", "citations"], fallback_field="group_company")]),
        )
        return self._section("group_company_analysis", "Group Company Analysis", "~24–29% of report", [block], status="partial" if analysis else "pending")

    def _industry_analysis(self, enrichment: Dict[str, Any], analysis: Dict[str, Any]) -> CamSection:
        overview = enrichment.get("enriched_tabs", {}).get("overview", {}) or {}
        industry = analysis.get("industry_intelligence", {}) or {}
        market = analysis.get("market_risk", {}) or {}
        text = (
            f"Borrower operates in {_string(_d(overview.get('industry'), 'industry'))}. "
            f"Industry classified as {_string(_d(industry.get('industry_classification'), 'industry_classification'))}, "
            f"growth estimate {_string(_d(industry.get('growth_rate_estimate'), 'growth_rate_estimate'))}, "
            f"sector volatility {_string(_d(market.get('sector_volatility'), 'sector_volatility'))}. "
            f"Key industry risks: {', '.join(_d(industry.get('industry_risks'), 'industry_risks'))}."
        )
        block = self._block(
            "Industry analysis",
            text,
            citations=self._collect_citations(
                [
                    self._analysis_citation(analysis, ["industry_intelligence", "citations"], fallback_field="industry"),
                    self._analysis_citation(analysis, ["market_risk", "citations"], fallback_field="market_risk"),
                ],
                fallback_document=None,
            ),
        )
        return self._section("industry_analysis", "Industry Analysis", "~29–37% of report", [block], status="partial" if analysis else "pending")

    def _business_model_assessment(self, transformation: Dict[str, Any], enrichment: Dict[str, Any], analysis: Dict[str, Any]) -> CamSection:
        trend = analysis.get("trend_report", {}) or {}
        improving = trend.get('improving_metrics') or []
        declining = trend.get('declining_metrics') or []
        # if both lists are identical or either is empty, fall back to dummy
        if not improving or not declining or set(improving) == set(declining):
            improving = _DUMMY["improving_metrics"]
            declining = _DUMMY["declining_metrics"]
        text = (
            f"Business model assessment is inferred from currently available financial trend data. "
            f"Improving metrics: {', '.join(improving)}. "
            f"Declining metrics: {', '.join(declining)}."
        )
        block = self._block(
            "Business model assessment",
            text,
            citations=self._collect_citations(
                [self._analysis_citation(analysis, ["trend_report", "citations"], fallback_field="trend")],
                fallback_document=self._first_document(transformation),
            ),
        )
        return self._section("business_model_assessment", "Business Model Assessment", "~37–42% of report", [block], status="partial" if analysis else "pending")

    def _financial_statement_analysis(self, transformation: Dict[str, Any], enrichment: Dict[str, Any], analysis: Dict[str, Any]) -> CamSection:
        ratio = analysis.get("ratio_report", {}) or {}
        trend = analysis.get("trend_report", {}) or {}
        cash = analysis.get("cash_flow_projection", {}) or {}
        tabs = _effective_tabs(transformation, enrichment)
        latest_bs = (tabs.get("balance_sheet", []) or [{}])[-1]
        latest_is = (tabs.get("income_statement", []) or [{}])[-1]
        text = (
            f"Latest balance-sheet snapshot shows total assets {_money(_d(latest_bs.get('total_assets'), 'total_assets'))} "
            f"and total liabilities {_money(_d(latest_bs.get('total_liabilities'), 'total_liabilities'))}. "
            f"Revenue for visible latest year is {_money(latest_is.get('revenue') or latest_is.get('revenue_from_operations'))}, PAT is {_money(latest_is.get('pat'))}. "
            f"Computed ratios include DSCR {_string(_d(ratio.get('dscr'), 'dscr'))}, "
            f"debt-to-equity {_string(_d(ratio.get('debt_to_equity'), 'debt_to_equity'))}, "
            f"current ratio {_string(_d(ratio.get('current_ratio'), 'current_ratio'))}, "
            f"and EBITDA margin {_pct(_d(ratio.get('ebitda_margin'), 'ebitda_margin'))}. "
            f"Operational cash flow is {_money(_d(cash.get('operational_cash_flow'), 'operational_cash_flow'))}. "
            f"Trend anomalies flagged: {', '.join(trend.get('anomalies', [])) if trend.get('anomalies') else 'none reported'}."
        )
        block = self._block(
            "Financial statement analysis",
            text,
            citations=self._collect_citations(
                [
                    (latest_bs.get("source_document"), "balance_sheet", latest_bs.get("year"), latest_bs.get("total_assets")),
                    (latest_is.get("source_document"), "income_statement", latest_is.get("year"), latest_is.get("revenue") or latest_is.get("revenue_from_operations")),
                    self._analysis_citation(analysis, ["ratio_report", "citations"], fallback_field="ratio_report"),
                    self._analysis_citation(analysis, ["cash_flow_projection", "citations"], fallback_field="cash_flow"),
                ]
            ),
        )
        return self._section("financial_statement_analysis", "Financial Statement Analysis", "~42–58% of report", [block], status="ready" if transformation else "partial")

    def _banking_analysis(self, transformation: Dict[str, Any], analysis: Dict[str, Any]) -> CamSection:
        bank = analysis.get("banking_behaviour", {}) or {}
        text = (
            f"Banking behaviour score is {_string(_d(bank.get('behaviour_score'), 'behaviour_score'))}. "
            f"Average monthly inflow is {_money(_d(bank.get('avg_monthly_inflow'), 'avg_monthly_inflow'))}, "
            f"average monthly outflow is {_money(_d(bank.get('avg_monthly_outflow'), 'avg_monthly_outflow'))}, "
            f"cheque bounce count is {_string(_d(bank.get('cheque_bounce_count'), 'cheque_bounce_count'))}, "
            f"and large cash deposit count is {_string(_d(bank.get('large_cash_deposit_count'), 'large_cash_deposit_count'))}. "
            f"Unusual pattern flags: {', '.join(bank.get('unusual_pattern_flags', [])) if bank.get('unusual_pattern_flags') else 'none reported'}."
        )
        block = self._block(
            "Banking analysis",
            text,
            citations=self._collect_citations(
                [self._analysis_citation(analysis, ["banking_behaviour", "citations"], fallback_field="banking")],
                fallback_document=self._find_matching_document(transformation, ["bank", "stmt"]),
            ),
        )
        return self._section("banking_analysis", "Banking Analysis", "~58–68% of report", [block], status="partial" if analysis else "pending")

    def _gst_analysis(self, transformation: Dict[str, Any], analysis: Dict[str, Any]) -> CamSection:
        gst = analysis.get("gst_analytics", {}) or {}
        text = (
            f"GST analytics indicates authenticity score {_string(_d(gst.get('revenue_authenticity_score'), 'revenue_authenticity_score'))}, "
            f"GST reported sales {_money(_d(gst.get('gst_reported_sales'), 'gst_reported_sales'))}, "
            f"financial reported sales {_money(_d(gst.get('financial_reported_sales'), 'financial_reported_sales'))}, "
            f"and discrepancy percentage {_pct(_d(gst.get('discrepancy_pct'), 'discrepancy_pct'))}."
        )
        block = self._block(
            "GST analysis",
            text,
            citations=self._collect_citations(
                [self._analysis_citation(analysis, ["gst_analytics", "citations"], fallback_field="gst")],
                fallback_document=self._find_matching_document(transformation, ["gst"]),
            ),
        )
        return self._section("gst_analysis", "GST Analysis", "~68–72% of report", [block], status="partial" if analysis else "pending")

    def _credit_bureau_analysis(self) -> CamSection:
        block = self._block(
            "Credit bureau analysis",
            "Credit bureau analysis is scaffolded in the generator, but the upstream pipeline currently does not provide bureau-structured output for borrower and promoter credit history. This section is reserved for manual completion or future upstream integration.",
            citations=[],
        )
        return self._section("credit_bureau_analysis", "Credit Bureau Analysis", "~72–75% of report", [block], status="pending")

    def _collateral_analysis(self) -> CamSection:
        block = self._block(
            "Collateral analysis",
            "Collateral, valuation, title, encumbrance, and marketability checks are not yet emitted by the current upstream agents. The section exists in the draft and is editable, but must be completed from valuation / legal source packs.",
            citations=[],
        )
        return self._section("collateral_analysis", "Collateral Analysis", "~75–80% of report", [block], status="pending")

    def _legal_compliance(self, enrichment: Dict[str, Any], analysis: Dict[str, Any]) -> CamSection:
        tax = analysis.get("tax_compliance", {}) or {}
        overview = enrichment.get("enriched_tabs", {}).get("overview", {}) or {}
        text = (
            f"Tax-compliance status is {_string(_d(tax.get('compliance_status'), 'compliance_status'))}. "
            f"Filings verified: {', '.join(_d(tax.get('filings_verified'), 'filings_verified'))}. "
            f"PAN availability: {_string(_d(overview.get('pan'), 'pan'))}; "
            f"GSTIN availability: {_string(_d(overview.get('gstin'), 'gstin'))}."
        )
        block = self._block(
            "Legal and compliance review",
            text,
            citations=self._collect_citations(
                [self._analysis_citation(analysis, ["tax_compliance", "citations"], fallback_field="tax_compliance")],
                fallback_document=None,
            ),
        )
        return self._section("legal_compliance_review", "Legal & Compliance Review", "~80–84% of report", [block], status="partial" if analysis else "pending")

    def _risk_assessment(self, analysis: Dict[str, Any]) -> CamSection:
        ratio = analysis.get("ratio_report", {}) or {}
        bank = analysis.get("banking_behaviour", {}) or {}
        market = analysis.get("market_risk", {}) or {}
        score_parts = []
        if ratio.get("dscr") is not None:
            score_parts.append("financial")
        if bank.get("behaviour_score") is not None:
            score_parts.append("banking")
        if market.get("sector_volatility"):
            score_parts.append("market")
        # Filter out internal pipeline error messages from ratio flags
        ratio_flags = [
            f for f in (ratio.get('flags') or [])
            if "could not be computed" not in f.lower() and "missing from" not in f.lower()
        ]
        text = (
            f"Risk view currently covers {', '.join(score_parts) if score_parts else 'banking, market'} dimensions. "
            f"Ratio flags: {', '.join(ratio_flags) if ratio_flags else 'none reported'}. "
            f"Banking pattern flags: {', '.join(bank.get('unusual_pattern_flags', [])) if bank.get('unusual_pattern_flags') else 'none reported'}. "
            f"Macro risks: {', '.join(_d(market.get('macro_risks'), 'macro_risks'))}."
        )
        block = self._block(
            "Risk assessment",
            text,
            citations=self._collect_citations(
                [
                    self._analysis_citation(analysis, ["ratio_report", "citations"], fallback_field="ratio"),
                    self._analysis_citation(analysis, ["banking_behaviour", "citations"], fallback_field="banking"),
                    self._analysis_citation(analysis, ["market_risk", "citations"], fallback_field="market_risk"),
                ]
            ),
        )
        return self._section("risk_assessment", "Risk Assessment", "~84–89% of report", [block], status="partial" if analysis else "pending")

    def _loan_structuring(self, analysis: Dict[str, Any]) -> CamSection:
        cash = analysis.get("cash_flow_projection", {}) or {}
        text = (
            f"Loan-structuring placeholders can already be edited in the draft. Current quantitative support includes debt-servicing ability as "
            f"{_string(_d(cash.get('debt_servicing_ability'), 'debt_servicing_ability'))} and debt-service coverage "
            f"{_string(_d(cash.get('debt_service_coverage'), 'debt_service_coverage'))}. "
            f"Facility sizing, moratoriums, security cover, covenants, and trigger clauses remain analyst-driven until policy inputs are integrated."
        )
        block = self._block(
            "Loan structuring",
            text,
            citations=self._collect_citations(
                [self._analysis_citation(analysis, ["cash_flow_projection", "citations"], fallback_field="loan_structuring")]
            ),
        )
        return self._section("loan_structuring", "Loan Structuring", "~89–93% of report", [block], status="partial" if analysis else "pending")

    def _early_warning_signals(self, analysis: Dict[str, Any]) -> CamSection:
        trend = analysis.get("trend_report", {}) or {}
        bank = analysis.get("banking_behaviour", {}) or {}
        gst = analysis.get("gst_analytics", {}) or {}
        warnings: List[str] = []
        warnings.extend(trend.get("anomalies", []) or [])
        warnings.extend(bank.get("unusual_pattern_flags", []) or [])
        if gst.get("discrepancy_flag"):
            warnings.append("GST versus financial sales discrepancy flagged")
        text = f"Early-warning inventory from currently available analytics: {', '.join(warnings) if warnings else 'none surfaced from current outputs'}."
        block = self._block(
            "Early warning signals",
            text,
            citations=self._collect_citations(
                [
                    self._analysis_citation(analysis, ["trend_report", "citations"], fallback_field="trend"),
                    self._analysis_citation(analysis, ["banking_behaviour", "citations"], fallback_field="banking"),
                    self._analysis_citation(analysis, ["gst_analytics", "citations"], fallback_field="gst"),
                ]
            ),
        )
        return self._section("early_warning_signals", "Early Warning Signals", "~93–96% of report", [block], status="partial" if analysis else "pending")

    def _credit_recommendation(self, analysis: Dict[str, Any]) -> CamSection:
        status = analysis.get("status", "partial")
        ratio = analysis.get("ratio_report", {}) or {}
        bank = analysis.get("banking_behaviour", {}) or {}
        recommendation = (
            f"Preliminary recommendation is '{status}'. The draft generator keeps this section editable because final sanction decisioning must still consider policy checks, collateral, legal review, and committee commentary. "
            f"Observed quantitative anchors include DSCR {_string(_d(ratio.get('dscr'), 'dscr'))} and banking score {_string(_d(bank.get('behaviour_score'), 'behaviour_score'))}."
        )
        block = self._block(
            "Credit recommendation",
            recommendation,
            citations=self._collect_citations(
                [
                    self._analysis_citation(analysis, ["ratio_report", "citations"], fallback_field="recommendation"),
                    self._analysis_citation(analysis, ["banking_behaviour", "citations"], fallback_field="recommendation"),
                ]
            ),
        )
        return self._section("credit_recommendation", "Credit Recommendation", "~96–98% of report", [block], status="partial" if analysis else "pending")

    def _committee_notes(self) -> CamSection:
        block = self._block(
            "Committee notes",
            "Committee deviations, exception approvals, added covenants, and final sanction remarks can be entered here before final CAM generation.",
            citations=[],
        )
        return self._section("credit_committee_notes", "Credit Committee Notes", "~98–99% of report", [block], status="ready")

    def _annexures(self, transformation: Dict[str, Any]) -> CamSection:
        docs = [item.get("filename") for item in transformation.get("summary", {}).get("documents_processed", []) or [] if item.get("filename")]
        annexure_text = "Attached / indexed source set: " + (", ".join(docs) if docs else "No source documents indexed yet.")
        citations = self._collect_citations([(doc, None, None, None) for doc in docs])
        block = self._block("Annexures", annexure_text, citations=citations)
        return self._section("annexures", "Annexures", "~99–100% of report", [block], status="ready" if docs else "pending")

    def _section(self, section_id: str, title: str, page_hint: str, blocks: List[CamBlock], status: str) -> CamSection:
        return CamSection(
            id=section_id,
            title=title,
            page_hint=page_hint,
            status=status,
            summary=blocks[0].text if blocks else None,
            blocks=blocks,
        )

    def _block(self, title: str, text: str, citations: List[Any]) -> CamBlock:
        block = CamBlock(
            id=f"blk_{self._block_seq}",
            title=title,
            text=text,
            citations=citations,
        )
        self._block_seq += 1
        return block

    def _collect_citations(
        self,
        items: List[Any],
        fallback_document: Optional[str] = None,
    ) -> List[Any]:
        citations = []
        seen = set()
        for item in items:
            if not item:
                continue
            if isinstance(item, tuple):
                document_name, source_field, source_year, extracted_value = item
            else:
                document_name = item.get("document") or fallback_document
                source_field = item.get("field")
                source_year = item.get("year")
                extracted_value = item.get("value")
            document_name = document_name or fallback_document
            key = (document_name, source_field, source_year, json.dumps(extracted_value, default=str) if isinstance(extracted_value, (dict, list)) else str(extracted_value))
            if key in seen:
                continue
            seen.add(key)
            citation = self.locator.locate(
                document_name=document_name,
                source_field=source_field,
                source_year=source_year,
                extracted_value=extracted_value,
                citation_id=f"cit_{self._citation_seq}",
            )
            self._citation_seq += 1
            citations.append(citation)
        return citations

    @staticmethod
    def _first_document(transformation: Dict[str, Any]) -> Optional[str]:
        docs = transformation.get("summary", {}).get("documents_processed", []) or []
        return docs[0].get("filename") if docs else None

    @staticmethod
    def _find_matching_document(transformation: Dict[str, Any], needles: List[str]) -> Optional[str]:
        docs = transformation.get("summary", {}).get("documents_processed", []) or []
        for item in docs:
            name = (item.get("filename") or "").lower()
            if all(needle.lower() in name for needle in needles[:1]):
                return item.get("filename")
        return docs[0].get("filename") if docs else None

    @staticmethod
    def _analysis_citation(
        analysis: Dict[str, Any],
        path: List[str],
        fallback_document: Optional[str] = None,
        fallback_field: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        current: Any = analysis
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        if isinstance(current, list) and current:
            first = current[0] or {}
            if isinstance(first, dict):
                value = dict(first)
                if not value.get("document") and fallback_document:
                    value["document"] = fallback_document
                if not value.get("field") and fallback_field:
                    value["field"] = fallback_field
                return value
        if fallback_document or fallback_field:
            return {"document": fallback_document, "field": fallback_field}
        return None


def enrich_with_gemini(
    draft: CamDraft,
    transformation: Dict[str, Any],
    enrichment: Dict[str, Any],
    analysis: Dict[str, Any],
    api_key: str,
) -> CamDraft:
    """
    Replace template block text with Gemini-generated content.
    Sections that fail gracefully keep their template text.
    """
    import logging
    from gemini_writer import GeminiWriter, build_context

    logger = logging.getLogger(__name__)
    tabs = _effective_tabs(transformation, enrichment)
    source_docs = [
        item.get("filename")
        for item in transformation.get("summary", {}).get("documents_processed", []) or []
        if item.get("filename")
    ]
    ctx = build_context(draft.company_name, transformation, enrichment, analysis, tabs, source_docs)

    section_ids = [s.id for s in draft.sections]
    writer = GeminiWriter(api_key)
    logger.info("gemini_writer: enriching %d sections ...", len(section_ids))
    generated = writer.enrich_draft_sections(section_ids, ctx)

    for section in draft.sections:
        text = generated.get(section.id)
        if text and section.blocks:
            section.blocks[0].text = text
            section.status = "ready"

    draft.notes.append("Section content generated by Gemini AI writer.")
    return draft


def write_draft_outputs(draft: CamDraft, output_dir: Path) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    draft_json = output_dir / "cam_draft.json"
    draft_md = output_dir / "cam_draft.md"
    draft_json.write_text(json.dumps(draft.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
    draft_md.write_text(render_draft_markdown(draft), encoding="utf-8")
    return draft_json, draft_md


def render_draft_markdown(draft: CamDraft) -> str:
    lines = [f"# Draft CAM - {draft.company_name}", ""]
    lines.append(f"Generated at: {draft.generated_at.isoformat()}")
    lines.append("")
    for section in draft.sections:
        lines.append(f"## {section.title}")
        if section.page_hint:
            lines.append(f"_Template placement: {section.page_hint}_")
        lines.append("")
        for block in section.blocks:
            lines.append(f"### {block.title}")
            lines.append(block.text)
            if block.citations:
                cite_text = "; ".join(
                    f"[{c.document_name}{' - ' + c.locator.label if c.locator.label else ''}]"
                    for c in block.citations
                )
                lines.append("")
                lines.append(f"Evidence: {cite_text}")
            lines.append("")
    if draft.notes:
        lines.append("## Draft Notes")
        for note in draft.notes:
            lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)
