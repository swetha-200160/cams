"""
scrapers/mca21_scraper.py

Scrapes MCA21 public portal for company registration data.

Free endpoints used:
  1. MCA21 Company Master Data  — JSON API (no auth)
     https://efiling.mca.gov.in/StakeholderV2/CompanyMasterData
  2. MCA21 company search HTML  — fallback when JSON API fails
     https://www.mca.gov.in/mcafoportal/companyLLPMasterData.do
  3. MCA21 Charge filing page   — separate CIN-keyed HTML page
     https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do

Fields retrieved:
  CIN, incorporation_date, registered_address, industry (NIC),
  directors (DIN + name), charges list, long_term_debt (summed from charges)

Why two endpoints for company master:
  The JSON API returns company identity fields quickly.
  Charge data lives on a separate HTML page — requires a second request
  keyed on CIN, so charges are only fetched when CIN is available.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import quote

from core.cache import Cache
from config.settings import CACHE_TTL, URLS
from core.state import ParsingError, ScraperError, SourceUnavailableError
from models.schemas import MissingField, RetrievedField, Source
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

_MCA21_CHARGES_URL = "https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do"
_MCA21_SEARCH_URL  = "https://www.mca.gov.in/mcafoportal/companyLLPMasterData.do"


class MCA21Scraper(BaseScraper):
    """
    Scrapes MCA21 for company master data, director info, and charge filings.

    Execution order inside scrape():
      1. _fetch_company_master_json()  — fast JSON API
      2. _fetch_company_master_html()  — HTML fallback if JSON fails
      3. _fetch_charges()              — charge page using resolved CIN
    """

    source = Source.MCA21
    domain = "www.mca.gov.in"

    # ── Public interface ───────────────────────────────────────────────────────

    async def scrape(
        self,
        company_name: Optional[str] = None,
        cin:          Optional[str] = None,
        **kwargs:     Any,
    ) -> list[RetrievedField]:
        """
        Entry point called by the scraper executor.
        Returns all RetrievedField objects found across all MCA21 endpoints.
        Never raises — logs warnings and returns partial results on failure.
        """
        if not company_name and not cin:
            raise ScraperError("MCA21Scraper requires company_name or cin")

        results: list[RetrievedField] = []

        # Step 1: Try JSON API first
        raw_json = await self._fetch_company_master_json(
            company_name=company_name, cin=cin
        )
        if raw_json:
            results.extend(self._parse_company_master_json(raw_json))
        else:
            # Step 2: HTML fallback
            logger.info("MCA21: JSON API returned nothing — trying HTML fallback")
            raw_html = await self._fetch_company_master_html(
                company_name=company_name, cin=cin
            )
            if raw_html:
                results.extend(self._parse_company_master_html(raw_html))

        # Resolve CIN from input or from what we just scraped
        resolved_cin = cin or next(
            (r.value for r in results if r.field_name == MissingField.CIN),
            None,
        )

        # Step 3: Charge filings (only possible with CIN)
        if resolved_cin:
            charges_html = await self._fetch_charges(resolved_cin)
            if charges_html:
                results.extend(self._parse_charges(charges_html))
        else:
            logger.warning(
                "MCA21: skipping charge lookup — CIN not available"
            )

        logger.info("MCA21Scraper: returning %d field(s)", len(results))
        return results

    # ── Step 1: JSON API ───────────────────────────────────────────────────────

    async def _fetch_company_master_json(
        self,
        company_name: Optional[str] = None,
        cin:          Optional[str] = None,
    ) -> Optional[dict]:
        """
        Hit MCA21 CompanyMasterData JSON endpoint.
        CIN lookup is exact; company name lookup is fuzzy.
        Returns parsed dict or None on any failure.
        """
        cache_key = Cache.make_key(
            "mca21_json", cin or company_name or "unknown"
        )

        params: dict[str, str] = {}
        if cin:
            params["cin"] = cin.strip().upper()
        elif company_name:
            params["companyName"] = company_name.strip()
        else:
            return None

        try:
            text = await self._get(
                url=URLS["mca21_master_data"],
                params=params,
                cache_key=cache_key,
                cache_ttl=CACHE_TTL["mca21"],
            )

            # MCA21 sometimes returns HTML error pages with HTTP 200
            if text.strip().startswith("<"):
                logger.warning("MCA21 JSON API returned HTML — treating as failure")
                return None

            data = json.loads(text)

            # API wraps payload in different envelope shapes
            if isinstance(data, dict):
                return (
                    data.get("companyMasterData")
                    or data.get("data")
                    or data
                )
            if isinstance(data, list) and data:
                return data[0]

            return None

        except (ScraperError, SourceUnavailableError, json.JSONDecodeError) as exc:
            logger.warning("MCA21 JSON API fetch failed: %s", exc)
            return None

    def _parse_company_master_json(self, raw: dict) -> list[RetrievedField]:
        """
        Map MCA21 JSON keys → RetrievedField objects.
        Handles multiple known key variants (MCA21 has inconsistent casing).
        """
        fields: list[RetrievedField] = []
        seen:   set[MissingField]    = set()

        # (json_key, MissingField, confidence)
        scalar_map: list[tuple[str, MissingField, float]] = [
            ("CIN",                        MissingField.CIN,                0.90),
            ("DATE_OF_REGISTRATION",       MissingField.INCORPORATION_DATE, 0.88),
            ("DATE_OF_INCORPORATION",      MissingField.INCORPORATION_DATE, 0.88),
            ("REGISTERED_ADDRESS",         MissingField.ADDRESS,            0.85),
            ("REG_ADDRESS",                MissingField.ADDRESS,            0.85),
            ("NIC_NAME",                   MissingField.INDUSTRY,           0.80),
            ("PRINCIPAL_BUSINESS_ACTIVITY",MissingField.INDUSTRY,           0.80),
            ("INDUSTRY",                   MissingField.INDUSTRY,           0.80),
        ]

        for json_key, missing_field, confidence in scalar_map:
            if missing_field in seen:
                continue
            value = raw.get(json_key)
            if value and str(value).strip():
                fields.append(RetrievedField(
                    field_name=missing_field,
                    value=str(value).strip(),
                    source=Source.MCA21,
                    confidence=confidence,
                ))
                seen.add(missing_field)
                logger.debug("MCA21 JSON: %s = %r", missing_field.value, value)

        # Directors — list of dicts under various key names
        for dir_key in ("DIRECTORS", "directors", "directorsList", "DIRECTOR_LIST"):
            directors_raw = raw.get(dir_key)
            if not (directors_raw and isinstance(directors_raw, list)):
                continue
            names: list[str] = []
            for d in directors_raw:
                if not isinstance(d, dict):
                    continue
                name = (
                    d.get("DIN_NAME")
                    or d.get("DIRECTOR_NAME")
                    or d.get("name")
                    or d.get("directorName")
                )
                if name:
                    names.append(str(name).strip())
            if names:
                fields.append(RetrievedField(
                    field_name=MissingField.DIRECTORS,
                    value=names,
                    source=Source.MCA21,
                    confidence=0.85,
                ))
                logger.debug("MCA21 JSON: directors = %s", names)
            break

        return fields

    # ── Step 2: HTML fallback ──────────────────────────────────────────────────

    async def _fetch_company_master_html(
        self,
        company_name: Optional[str] = None,
        cin:          Optional[str] = None,
    ) -> Optional[str]:
        """
        HTML fallback via Playwright — MCA portal returns 403 to plain httpx.
        Playwright renders in real headless Chromium, bypassing bot detection.
        """
        query     = cin or company_name or ""
        cache_key = Cache.make_key("mca21_html", query)
        url       = f"{_MCA21_SEARCH_URL}?companyName={quote(query)}"

        try:
            return await self._get_playwright(
                url=url,
                cache_key=cache_key,
                cache_ttl=CACHE_TTL["mca21"],
            )
        except SourceUnavailableError as exc:
            logger.warning("MCA21 HTML Playwright fallback failed: %s", exc)
            return None

    def _parse_company_master_html(self, html: str) -> list[RetrievedField]:
        """
        Parse MCA21 search result HTML.
        Extracts data from labelled <table> rows.
        """
        fields: list[RetrievedField] = []

        try:
            soup = self._parse_html(html)

            # CIN — 21-char regex pattern
            cin_match = re.search(
                r'\b[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b',
                soup.get_text(),
            )
            if cin_match:
                fields.append(RetrievedField(
                    field_name=MissingField.CIN,
                    value=cin_match.group(),
                    source=Source.MCA21,
                    confidence=0.80,
                ))

            # Table-row label → field mapping
            label_field_map = {
                "date of incorporation":       MissingField.INCORPORATION_DATE,
                "date of registration":        MissingField.INCORPORATION_DATE,
                "registered address":          MissingField.ADDRESS,
                "principal business activity": MissingField.INDUSTRY,
                "nic description":             MissingField.INDUSTRY,
            }
            seen: set[MissingField] = set()

            for row in soup.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                label = cells[0].get_text(strip=True).lower()
                value = cells[-1].get_text(strip=True)

                for key, missing_field in label_field_map.items():
                    if key in label and value and missing_field not in seen:
                        fields.append(RetrievedField(
                            field_name=missing_field,
                            value=value,
                            source=Source.MCA21,
                            confidence=0.78,
                        ))
                        seen.add(missing_field)
                        logger.debug(
                            "MCA21 HTML: %s = %r", missing_field.value, value
                        )
                        break

        except Exception as exc:
            raise ParsingError(f"MCA21 HTML parse failed: {exc}") from exc

        return fields

    # ── Step 3: Charge filings ─────────────────────────────────────────────────

    async def _fetch_charges(self, cin: str) -> Optional[str]:
        """
        Fetch MCA21 charge filing page for the given CIN.
        Charge records indicate secured loans = long_term_debt signal.
        """
        cache_key = Cache.make_key("mca21_charges", cin)

        try:
            return await self._get(
                url=_MCA21_CHARGES_URL,
                params={"cin": cin.strip().upper()},
                cache_key=cache_key,
                cache_ttl=CACHE_TTL["mca21"],
            )
        except ScraperError as exc:
            logger.warning(
                "MCA21 charges fetch failed for CIN %s: %s", cin, exc
            )
            return None

    def _parse_charges(self, html: str) -> list[RetrievedField]:
        """
        Parse MCA21 charge filing HTML into a structured list.
        Each charge record represents a secured loan filing.

        Typical MCA21 charge table columns:
          Charge ID | Charge Holder | Amount | Date Created | Status (Open/Closed)
        """
        fields: list[RetrievedField] = []

        try:
            soup    = self._parse_html(html)
            charges: list[dict] = []

            # Find charge-related tables (by id, class, or header content)
            candidate_tables = (
                soup.find_all("table", id=re.compile(r"charge", re.I))
                or soup.find_all("table", class_=re.compile(r"charge", re.I))
                or soup.find_all("table")
            )

            for table in candidate_tables:
                rows    = table.find_all("tr")
                if not rows:
                    continue

                headers = [
                    th.get_text(strip=True).lower()
                    for th in rows[0].find_all(["th", "td"])
                ]

                # Only process tables that look like charge data
                has_charge_header = any(
                    kw in " ".join(headers)
                    for kw in ("charge", "amount", "holder", "lender")
                )
                if not has_charge_header:
                    continue

                for row in rows[1:]:
                    cells = row.find_all("td")
                    if not cells:
                        continue
                    entry: dict[str, str] = {
                        headers[i]: cells[i].get_text(strip=True)
                        for i in range(min(len(headers), len(cells)))
                    }
                    if any(v for v in entry.values()):
                        charges.append(entry)

            if not charges:
                logger.debug("MCA21 charges: no charge records found")
                return fields

            # Store full charge list for citation audit trail
            fields.append(RetrievedField(
                field_name=MissingField.CHARGES,
                value=charges,
                source=Source.MCA21,
                confidence=0.85,
            ))
            logger.debug("MCA21 charges: %d records found", len(charges))

            # Sum open charge amounts → long_term_debt estimate
            total = self._sum_charge_amounts(charges)
            if total is not None:
                fields.append(RetrievedField(
                    field_name=MissingField.LONG_TERM_DEBT,
                    value=total,
                    source=Source.MCA21,
                    confidence=0.70,  # approximation — charges ≠ exact outstanding
                ))
                logger.debug(
                    "MCA21: long_term_debt estimate from charges = %.2f", total
                )

        except Exception as exc:
            raise ParsingError(f"MCA21 charge parse failed: {exc}") from exc

        return fields

    @staticmethod
    def _sum_charge_amounts(charges: list[dict]) -> Optional[float]:
        """
        Sum numeric amounts from charge records.
        Only sums records whose status suggests open/outstanding loans.
        Returns None if no parseable amounts found.
        """
        amount_keys = (
            "amount",
            "charge amount",
            "amount (in rupees)",
            "amount in inr",
        )
        open_statuses = {"open", "active", "subsisting", ""}

        total      = 0.0
        found_any  = False

        for charge in charges:
            # Skip closed/satisfied charges
            status = charge.get("status", "").lower().strip()
            if status and status not in open_statuses:
                continue

            for key in amount_keys:
                raw_val = charge.get(key, "")
                if not raw_val:
                    continue
                cleaned = re.sub(r"[^\d.]", "", raw_val)
                try:
                    total     += float(cleaned)
                    found_any  = True
                    break
                except ValueError:
                    continue

        return total if found_any else None
