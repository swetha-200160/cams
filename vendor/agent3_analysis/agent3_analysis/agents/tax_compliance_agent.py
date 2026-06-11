"""
agents/tax_compliance_agent.py
Agent 3.7 — Tax Compliance Agent
Wave 2 (parallel): Verifies ITR filings and cross-checks declared income.

Degrades gracefully when itr_filings is empty (current PoC state).
"""

from __future__ import annotations

import logging
from typing import List

from agent3_analysis.schemas.input_schema import Agent2Output
from agent3_analysis.schemas.output_schema import (
    AgentStatus,
    Citation,
    DataQuality,
    TaxComplianceReport,
)

logger = logging.getLogger(__name__)


def run(input_data: Agent2Output) -> TaxComplianceReport:
    """
    Verify tax filing compliance and cross-check declared income.

    Args:
        input_data: Validated Agent 2 output.

    Returns:
        TaxComplianceReport.
    """
    logger.info("Agent 3.7 — Tax Compliance started.")

    itr_filings = input_data.itr_filings or []
    overview = input_data.enriched_overview
    citations: List[Citation] = []
    flags: List[str] = []
    filings_verified: List[str] = []

    if not itr_filings:
        logger.warning("Agent 3.7 — No ITR filings provided. Returning INSUFFICIENT.")
        pan_note = f" (PAN: {overview.pan})" if overview and overview.pan else ""
        return TaxComplianceReport(
            status=AgentStatus.SKIPPED,
            data_quality=DataQuality.INSUFFICIENT,
            compliance_status="unknown",
            narrative=(
                f"Tax compliance check skipped: no ITR filing data provided by Agent 2{pan_note}. "
                "Compliance status will be determined once Income Tax Return data is available."
            ),
        )

    # ── Detect format: rich itr_data vs legacy year-summary ──────
    first = itr_filings[0] if itr_filings else {}
    is_rich_format = isinstance(first, dict) and first.get("__rich_itr_data__")

    if is_rich_format:
        # ── Rich format: consolidated itr_data from transformation agent ──
        itr_data = {k: v for k, v in first.items() if k != "__rich_itr_data__"}

        period         = itr_data.get("period") or "unknown"
        filing_status  = itr_data.get("filing_status") or {}
        income         = itr_data.get("income") or {}
        tax            = itr_data.get("tax") or {}
        comp_flags     = itr_data.get("compliance_flags") or {}

        declared_income   = income.get("taxable_income") or income.get("reported_total_income")
        net_payable       = tax.get("net_payable")
        filed_on_time     = filing_status.get("filed_on_time")
        section           = filing_status.get("section") or "unknown"
        audit_applicable  = filing_status.get("audit_applicable")
        audit_completed   = filing_status.get("audit_completed")

        # Build flags from compliance_flags
        if comp_flags.get("filing_delay"):
            flags.append(f"ITR for {period} filed late (section: {section}).")
        if comp_flags.get("disallowances_present"):
            flags.append(f"Income disallowances detected: reported income exceeds taxable income.")
        if not comp_flags.get("tax_fully_paid"):
            net_val = f"{net_payable:,.0f}" if net_payable is not None else "unknown"
            flags.append(f"Tax not fully paid. Net payable: {net_val}.")
        if audit_applicable and not audit_completed:
            flags.append("Audit applicable but audit report not filed.")

        filings_verified.append(f"{period}: section {section}")

        # Income cross-check against P&L
        income_cross_check_flag = False
        pl_income_by_year = {
            e.year: (e.revenue_from_operations or 0) - (e.cost_of_material or 0) - (e.employee_benefit_expense or 0)
            for e in (input_data.income_statement or [])
            if e.year
        }
        if declared_income and pl_income_by_year:
            closest_pl = next(iter(pl_income_by_year.values()))
            if closest_pl != 0:
                diff_pct = abs(declared_income - closest_pl) / abs(closest_pl)
                if diff_pct > 0.15:
                    income_cross_check_flag = True
                    flags.append(
                        f"Declared income ({declared_income:,.0f}) differs from "
                        f"P&L computed income ({closest_pl:,.0f}) by {diff_pct:.1%}."
                    )

        # Compliance status
        if not flags:
            compliance_status = "compliant"
        elif comp_flags.get("filing_delay") or not comp_flags.get("tax_fully_paid"):
            compliance_status = "non-compliant"
        else:
            compliance_status = "partial"

        quality = DataQuality.COMPLETE if declared_income is not None else DataQuality.PARTIAL

        logger.info(
            "Agent 3.7 — Rich format. Period=%s. Status=%s. Flags=%d.",
            period, compliance_status, len(flags),
        )

        return TaxComplianceReport(
            status=AgentStatus.SUCCESS,
            data_quality=quality,
            compliance_status=compliance_status,
            filings_verified=filings_verified,
            income_cross_check_flag=income_cross_check_flag,
            flags=flags,
            narrative=(
                f"Tax compliance check completed for {period} (section {section}). "
                f"Status: {compliance_status}. "
                + (f"Flags: {'; '.join(flags)}" if flags else "No issues detected.")
            ),
            citations=citations,
        )

    # ── Legacy format: [{year, filing_date, declared_income, tax_paid, status}] ──
    income_cross_check_flag = False
    compliance_statuses: List[str] = []

    pl_income_by_year = {
        e.year: (e.revenue_from_operations or 0) - (e.cost_of_material or 0) - (e.employee_benefit_expense or 0)
        for e in (input_data.income_statement or [])
        if e.year
    }

    for entry in itr_filings:
        if not isinstance(entry, dict):
            continue
        year = entry.get("year")
        status = entry.get("status", "unknown")
        declared_income = entry.get("declared_income")

        if year:
            filings_verified.append(f"FY{year}: {status}")
            citations.append(Citation(
                document=f"ITR Filing FY{year}",
                field="declared_income",
                year=year,
                source="ITR",
            ))

        if status in ("pending", "missing"):
            flags.append(f"ITR for FY{year} is {status}.")

        if declared_income and year and year in pl_income_by_year:
            pl_income = pl_income_by_year[year]
            if pl_income != 0:
                diff_pct = abs(declared_income - pl_income) / abs(pl_income)
                if diff_pct > 0.15:
                    income_cross_check_flag = True
                    flags.append(
                        f"FY{year}: Declared ITR income ({declared_income:,.0f}) differs from "
                        f"P&L computed income ({pl_income:,.0f}) by {diff_pct:.1%}."
                    )

        compliance_statuses.append(status)

    if all(s == "filed" for s in compliance_statuses):
        compliance_status = "compliant"
    elif any(s in ("pending", "missing") for s in compliance_statuses):
        compliance_status = "non-compliant"
    else:
        compliance_status = "partial"

    if overview and overview.pan:
        citations.append(Citation(document="enriched_overview", field="pan", source="ZAUBA"))

    quality = DataQuality.COMPLETE if filings_verified and not flags else DataQuality.PARTIAL

    narrative = (
        f"Tax compliance check completed. Status: {compliance_status}. "
        f"Filings reviewed: {', '.join(filings_verified) or 'none'}. "
        + (f"Flags: {'; '.join(flags)}" if flags else "No issues detected.")
    )

    logger.info(
        "Agent 3.7 — Completed. Status=%s. Flags=%d. Income cross-check flag=%s.",
        compliance_status, len(flags), income_cross_check_flag,
    )

    return TaxComplianceReport(
        status=AgentStatus.SUCCESS,
        data_quality=quality,
        compliance_status=compliance_status,
        filings_verified=filings_verified,
        income_cross_check_flag=income_cross_check_flag,
        flags=flags,
        narrative=narrative,
        citations=citations,
    )
