"""
scrapers/ecourts_scraper.py

Scrapes eCourts India public portal for legal cases involving a company.

Free source — no auth required.
Base: https://services.ecourts.gov.in/ecourtindia_v6/

Two search strategies:
  1. POST party-name search (petitioner side)
  2. POST party-name search (respondent side)
Both are run — company may appear on either side of a case.

Fields retrieved:
  legal_cases — structured list:
    {case_number, case_type, court, filing_date, status, party_role,
     petitioner, respondent, next_hearing_date}

Why this matters for CAM:
  Active litigation is a key credit risk signal.
  Defaults, winding-up petitions, and fraud cases directly impact loan decisions.
  eCourts is the only free public source for Indian court case data.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from core.cache import Cache
from config.settings import CACHE_TTL
from core.state import ParsingError, ScraperError
from models.schemas import MissingField, RetrievedField, Source
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

_ECOURTS_BASE      = "https://services.ecourts.gov.in/ecourtindia_v6/"
_ECOURTS_PARTY_URL = f"{_ECOURTS_BASE}?p=casestatus/index"

# Party type codes used by eCourts API
_PARTY_PETITIONER  = "1"
_PARTY_RESPONDENT  = "2"

# Case types that are high-risk for credit appraisal
_HIGH_RISK_CASE_TYPES = {
    "winding up", "insolvency", "fraud", "cheque bounce",
    "dishonour", "recovery", "npa", "debt recovery"
}


class ECourtsScraper(BaseScraper):
    """
    Fetches legal case records from eCourts India public portal.
    Searches both petitioner and respondent sides.
    """

    source = Source.ECOURTS
    domain = "ecourts.gov.in"

    # ── Public interface ───────────────────────────────────────────────────────

    async def scrape(
        self,
        company_name: Optional[str] = None,
        cin:          Optional[str] = None,
        pan:          Optional[str] = None,
        **kwargs:     Any,
    ) -> list[RetrievedField]:
        """
        Entry point. Searches by company name on both petitioner
        and respondent sides, merges and deduplicates results.
        """
        if not company_name:
            logger.warning("ECourtsScraper: no company_name — skipping")
            return []

        identifier = cin or pan or company_name
        all_cases:  list[dict] = []

        # Search as petitioner
        petitioner_cases = await self._search_party(
            party_name=company_name,
            party_type=_PARTY_PETITIONER,
            cache_key=Cache.make_key("ecourts_pet", identifier),
        )
        all_cases.extend(self._tag_role(petitioner_cases, "petitioner"))

        # Search as respondent
        respondent_cases = await self._search_party(
            party_name=company_name,
            party_type=_PARTY_RESPONDENT,
            cache_key=Cache.make_key("ecourts_res", identifier),
        )
        all_cases.extend(self._tag_role(respondent_cases, "respondent"))

        # Deduplicate by case number
        all_cases = self._deduplicate(all_cases)

        if not all_cases:
            logger.info(
                "ECourtsScraper: no cases found for '%s'", company_name
            )
            return []

        high_risk = self._flag_high_risk(all_cases)
        if high_risk:
            logger.warning(
                "ECourtsScraper: %d HIGH-RISK case(s) found for '%s': %s",
                len(high_risk),
                company_name,
                [c.get("case_type", "") for c in high_risk],
            )

        logger.info(
            "ECourtsScraper: %d total case(s) found (%d high-risk)",
            len(all_cases), len(high_risk),
        )

        return [
            RetrievedField(
                field_name=MissingField.LEGAL_CASES,
                value=all_cases,
                source=Source.ECOURTS,
                confidence=0.82,
            )
        ]

    # ── Private: search ────────────────────────────────────────────────────────

    async def _search_party(
        self,
        party_name: str,
        party_type: str,
        cache_key:  str,
    ) -> list[dict]:
        """
        POST to eCourts party-name search endpoint.
        Returns list of raw case dicts or empty list on failure.
        """
        try:
            text = await self._post(
                url=_ECOURTS_PARTY_URL,
                data={
                    "party_name":  party_name.strip(),
                    "party_type":  party_type,
                    "state_code":  "",     # blank = all states
                    "dist_code":   "",
                    "court_code":  "",
                    "captcha":     "",     # public endpoint — no captcha for API
                },
                cache_key=cache_key,
                cache_ttl=CACHE_TTL["ecourts"],
            )

            if not text or text.strip().startswith("<html"):
                # HTML response = eCourts returned an error page
                logger.debug(
                    "eCourts party_type=%s returned HTML — no cases or blocked",
                    party_type,
                )
                return []

            data = json.loads(text)

            # eCourts may wrap in envelope
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return (
                    data.get("cases")
                    or data.get("data")
                    or data.get("result")
                    or []
                )

            return []

        except (ScraperError, json.JSONDecodeError) as exc:
            logger.warning(
                "eCourts search failed (party_type=%s): %s", party_type, exc
            )
            return []

    # ── Private: parsing ───────────────────────────────────────────────────────

    def _parse_case(self, raw: dict) -> dict:
        """
        Normalise a raw eCourts case dict into a consistent schema.
        Handles multiple known key variants from different eCourts API versions.
        """
        return {
            "case_number":       (
                raw.get("case_no")
                or raw.get("caseNumber")
                or raw.get("CNR_NUMBER")
                or ""
            ),
            "case_type":         (
                raw.get("case_type")
                or raw.get("caseType")
                or raw.get("CASE_TYPE")
                or ""
            ),
            "court":             (
                raw.get("court_name")
                or raw.get("courtName")
                or raw.get("COURT_NAME")
                or ""
            ),
            "filing_date":       (
                raw.get("date_of_filing")
                or raw.get("filingDate")
                or raw.get("DATE_OF_FILING")
                or ""
            ),
            "status":            (
                raw.get("case_status")
                or raw.get("caseStatus")
                or raw.get("STATUS")
                or ""
            ),
            "petitioner":        (
                raw.get("petitioner")
                or raw.get("pet_name")
                or raw.get("PETITIONER_NAME")
                or ""
            ),
            "respondent":        (
                raw.get("respondent")
                or raw.get("res_name")
                or raw.get("RESPONDENT_NAME")
                or ""
            ),
            "next_hearing_date": (
                raw.get("next_hearing_date")
                or raw.get("nextHearingDate")
                or raw.get("NEXT_HEARING_DATE")
                or ""
            ),
        }

    @staticmethod
    def _tag_role(cases: list[dict], role: str) -> list[dict]:
        """Add party_role field to each case dict."""
        parsed = []
        for c in cases:
            if isinstance(c, dict):
                entry = {
                    "case_number":       c.get("case_no") or c.get("caseNumber") or c.get("CNR_NUMBER") or "",
                    "case_type":         c.get("case_type") or c.get("caseType") or "",
                    "court":             c.get("court_name") or c.get("courtName") or "",
                    "filing_date":       c.get("date_of_filing") or c.get("filingDate") or "",
                    "status":            c.get("case_status") or c.get("caseStatus") or "",
                    "petitioner":        c.get("petitioner") or c.get("pet_name") or "",
                    "respondent":        c.get("respondent") or c.get("res_name") or "",
                    "next_hearing_date": c.get("next_hearing_date") or c.get("nextHearingDate") or "",
                    "party_role":        role,
                }
                # Only keep non-empty records
                if any(v for k, v in entry.items() if k != "party_role"):
                    parsed.append(entry)
        return parsed

    @staticmethod
    def _deduplicate(cases: list[dict]) -> list[dict]:
        """
        Remove duplicate cases by case_number.
        Keeps first occurrence (petitioner side preferred).
        """
        seen:   set[str]   = set()
        result: list[dict] = []
        for case in cases:
            key = case.get("case_number", "").strip()
            if key and key not in seen:
                seen.add(key)
                result.append(case)
            elif not key:
                result.append(case)   # keep cases without number (can't dedup)
        return result

    @staticmethod
    def _flag_high_risk(cases: list[dict]) -> list[dict]:
        """
        Return subset of cases that match high-risk keywords.
        Used for logging — does not modify the cases list.
        """
        flagged = []
        for case in cases:
            case_type = case.get("case_type", "").lower()
            if any(kw in case_type for kw in _HIGH_RISK_CASE_TYPES):
                flagged.append(case)
        return flagged
