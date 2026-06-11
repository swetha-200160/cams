"""
scrapers/zauba_scraper.py

Scrapes Zauba Corp (zaubacorp.com) as a fallback for MCA21 data.

Zauba is a public aggregator of MCA21 data — no login required for basic info.
Only called by the scraper executor when MCA21 has failed or returned
incomplete data for a field.

Two-step process:
  1. Search page   → get the company's Zauba slug URL
  2. Company page  → extract full details (CIN, address, directors, date)

Fields retrieved:
  CIN, incorporation_date, registered_address, directors (partial),
  company status (Active / Struck Off / Dormant)

Confidence is 0.70 (lower than MCA21 0.85) because:
  - Zauba is a third-party aggregator
  - Data refresh lag varies by company
  - Director list may be outdated
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup, Tag

from core.cache import Cache
from config.settings import CACHE_TTL
from core.state import ParsingError, ScraperError, SourceUnavailableError
from models.schemas import MissingField, RetrievedField, Source
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

_ZAUBA_BASE        = "https://www.zaubacorp.com"
_ZAUBA_SEARCH_URL  = f"{_ZAUBA_BASE}/company-search"
_ZAUBA_COMPANY_URL = f"{_ZAUBA_BASE}/company"

# CIN regex — 21-char Indian company identification number
_CIN_PATTERN = re.compile(r'\b[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b')


class ZaubaScraper(BaseScraper):
    """
    Fallback scraper — HTML parse of Zauba Corp public pages.

    Execution order:
      1. _search()          → get Zauba company URL slug
      2. _fetch_company()   → load full company detail page
      3. _parse_company()   → extract fields from HTML
    """

    source = Source.ZAUBA
    domain = "www.zaubacorp.com"

    # ── Public interface ───────────────────────────────────────────────────────

    async def scrape(
        self,
        company_name: Optional[str] = None,
        cin:          Optional[str] = None,
        **kwargs:     Any,
    ) -> list[RetrievedField]:
        """
        Entry point. Uses CIN for direct URL construction (faster)
        or company name for search-then-fetch (two requests).
        """
        if not company_name and not cin:
            raise ScraperError("ZaubaScraper requires company_name or cin")

        identifier = cin or company_name or "unknown"

        # Step 1: Get company page URL
        company_url = None

        if cin:
            # Zauba URL pattern: /company/<NAME-SLUG>/<CIN>
            # We can search by CIN directly
            company_url = await self._search_by_cin(cin)

        if not company_url and company_name:
            company_url = await self._search_by_name(company_name)

        if not company_url:
            logger.warning(
                "ZaubaScraper: could not find company page for '%s'",
                identifier,
            )
            return []

        # Step 2: Fetch and parse company detail page
        cache_key = Cache.make_key("zauba_company", identifier)
        html      = await self._fetch_company_page(company_url, cache_key)

        if not html:
            return []

        # Step 3: Parse
        return self._parse_company_page(html)

    # ── Step 1: Search ─────────────────────────────────────────────────────────

    async def _search_by_cin(self, cin: str) -> Optional[str]:
        """
        Search Zauba by CIN via Playwright — plain httpx always gets 403.
        """
        cache_key = Cache.make_key("zauba_search_cin", cin)
        url       = f"{_ZAUBA_SEARCH_URL}?q={quote(cin.upper())}"

        try:
            html = await self._get_playwright(
                url=url,
                cache_key=cache_key,
                cache_ttl=CACHE_TTL["zauba"],
            )
            return self._extract_first_result_url(html)
        except SourceUnavailableError as exc:
            logger.warning("Zauba CIN search failed for %s: %s", cin, exc)
            return None

    async def _search_by_name(self, company_name: str) -> Optional[str]:
        """
        Search Zauba by company name via Playwright — plain httpx always gets 403.
        """
        cache_key = Cache.make_key("zauba_search_name", company_name[:30])
        url       = f"{_ZAUBA_SEARCH_URL}?q={quote(company_name)}"

        try:
            html = await self._get_playwright(
                url=url,
                cache_key=cache_key,
                cache_ttl=CACHE_TTL["zauba"],
            )
            return self._extract_first_result_url(html)
        except SourceUnavailableError as exc:
            logger.warning("Zauba name search failed for '%s': %s", company_name, exc)
            return None

    def _extract_first_result_url(self, html: str) -> Optional[str]:
        """
        Parse Zauba search results page and return the first company result URL.
        Zauba search results are in a table with class 'table'.
        Result links follow pattern: /company/<SLUG>/<CIN>
        """
        try:
            soup = self._parse_html(html)

            # Look for links matching Zauba company URL pattern
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if re.match(r'^/company/[^/]+/[UL]\d{5}', href):
                    full_url = f"{_ZAUBA_BASE}{href}"
                    logger.debug("Zauba: found company URL = %s", full_url)
                    return full_url

            # Fallback: any link in search results table
            results_table = soup.find("table", class_=re.compile(r"table", re.I))
            if results_table:
                first_link = results_table.find("a", href=True)
                if first_link:
                    href = first_link["href"]
                    if href.startswith("/company/"):
                        return f"{_ZAUBA_BASE}{href}"

            logger.debug("Zauba: no company URL found in search results")
            return None

        except Exception as exc:
            logger.warning("Zauba search result parse failed: %s", exc)
            return None

    # ── Step 2: Fetch company page ─────────────────────────────────────────────

    async def _fetch_company_page(
        self, url: str, cache_key: str
    ) -> Optional[str]:
        """Fetch Zauba company detail page via Playwright."""
        try:
            return await self._get_playwright(
                url=url,
                cache_key=cache_key,
                cache_ttl=CACHE_TTL["zauba"],
            )
        except SourceUnavailableError as exc:
            logger.warning("Zauba company page fetch failed: %s", exc)
            return None

    # ── Step 3: Parse company page ─────────────────────────────────────────────

    def _parse_company_page(self, html: str) -> list[RetrievedField]:
        """
        Parse Zauba company detail page HTML.

        Zauba page structure:
          - Company header: name, CIN, status
          - Details table: registration date, address, business type
          - Directors section: table of DIN, name, designation
        """
        fields: list[RetrievedField] = []

        try:
            soup = self._parse_html(html)

            fields.extend(self._extract_cin(soup))
            fields.extend(self._extract_table_fields(soup))
            fields.extend(self._extract_directors(soup))

        except Exception as exc:
            raise ParsingError(f"Zauba company page parse failed: {exc}") from exc

        logger.info("ZaubaScraper: extracted %d field(s)", len(fields))
        return fields

    def _extract_cin(self, soup: BeautifulSoup) -> list[RetrievedField]:
        """Extract CIN from page text using regex pattern."""
        fields: list[RetrievedField] = []
        text   = soup.get_text()
        match  = _CIN_PATTERN.search(text)

        if match:
            cin_val = match.group()
            fields.append(RetrievedField(
                field_name=MissingField.CIN,
                value=cin_val,
                source=Source.ZAUBA,
                confidence=0.70,
            ))
            logger.debug("Zauba: CIN = %s", cin_val)

        return fields

    def _extract_table_fields(self, soup: BeautifulSoup) -> list[RetrievedField]:
        """
        Extract labelled fields from Zauba company detail tables.

        Zauba uses <tr> rows with a label <td> and a value <td>.
        Example:
          <tr><td>Date of Incorporation</td><td>15 Mar 2010</td></tr>
        """
        fields: list[RetrievedField] = []
        seen:   set[MissingField]    = set()

        # Label text (lowercase) → (MissingField, confidence)
        label_map: dict[str, tuple[MissingField, float]] = {
            "date of incorporation": (MissingField.INCORPORATION_DATE, 0.70),
            "date of registration":  (MissingField.INCORPORATION_DATE, 0.70),
            "registered address":    (MissingField.ADDRESS, 0.68),
            "address":               (MissingField.ADDRESS, 0.65),
            "principal business":    (MissingField.INDUSTRY, 0.65),
            "class of company":      (MissingField.INDUSTRY, 0.60),
            "industry":              (MissingField.INDUSTRY, 0.65),
        }

        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            label = cells[0].get_text(strip=True).lower()
            value = cells[-1].get_text(strip=True)

            if not value:
                continue

            for key, (missing_field, confidence) in label_map.items():
                if key in label and missing_field not in seen:
                    fields.append(RetrievedField(
                        field_name=missing_field,
                        value=value,
                        source=Source.ZAUBA,
                        confidence=confidence,
                    ))
                    seen.add(missing_field)
                    logger.debug(
                        "Zauba table: %s = %r", missing_field.value, value
                    )
                    break

        return fields

    def _extract_directors(self, soup: BeautifulSoup) -> list[RetrievedField]:
        """
        Extract director names from Zauba directors section.

        Zauba renders directors in a table with columns:
          DIN | Director Name | Designation | Date of Appointment
        """
        fields: list[RetrievedField] = []

        # Find directors section header
        director_header = soup.find(
            string=re.compile(r"directors?", re.I)
        )
        if not director_header:
            return fields

        # Find the next table after the directors header
        parent  = director_header.find_parent()
        table   = None
        current = parent

        for _ in range(10):   # walk up/forward max 10 elements
            if not current:
                break
            table = current.find_next("table")
            if table:
                break
            current = current.parent

        if not table:
            return fields

        rows    = table.find_all("tr")
        names:  list[str] = []

        for row in rows[1:]:   # skip header row
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            # Director name is typically in the 2nd column (index 1)
            # DIN is in the 1st column — 8-digit number
            for cell in cells:
                text = cell.get_text(strip=True)
                # Skip DIN (numeric), dates, and short designations
                if (
                    text
                    and not text.isdigit()
                    and len(text) > 5
                    and not re.match(r'\d{2}[-/]\d{2}[-/]\d{4}', text)
                    and text.upper() not in ("DIRECTOR", "MANAGING DIRECTOR",
                                              "CEO", "CFO", "DESIGNATION")
                ):
                    names.append(text)
                    break   # take first valid cell per row

        if names:
            fields.append(RetrievedField(
                field_name=MissingField.DIRECTORS,
                value=names,
                source=Source.ZAUBA,
                confidence=0.65,    # lower — Zauba director data may be stale
            ))
            logger.debug("Zauba: directors = %s", names)

        return fields
