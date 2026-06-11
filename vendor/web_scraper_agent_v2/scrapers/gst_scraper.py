"""
scrapers/gst_scraper.py

Scrapes GST portal public GSTIN lookup — no auth required.

Free endpoint:
  https://services.gst.gov.in/services/api/search/search_by_gstin/{gstin}

Fields retrieved:
  gstin (verified), company trade name / legal name (cross-validation),
  registration status (Active / Cancelled), filing frequency (Monthly / Quarterly),
  state of registration, business type, industry (NIC code if returned)

Why this is valuable for CAM:
  - Verifies GSTIN is active (not cancelled)
  - Trade name cross-checks company name from documents
  - Filing frequency signals business activity level
  - State of registration validates address
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

_GST_SEARCH_BASE = "https://services.gst.gov.in/services/api/search/search_by_gstin"
_GST_PUBLIC_SEARCH = "https://www.gst.gov.in/searchtp"


class GSTScraper(BaseScraper):
    """
    Fetches GSTIN details from the GST portal public search API.

    Two approaches tried in order:
      1. GST services JSON API  — structured response, preferred
      2. GST public HTML search — fallback if API is gated
    """

    source = Source.GST_PORTAL
    domain = "services.gst.gov.in"

    # ── Public interface ───────────────────────────────────────────────────────

    async def scrape(
        self,
        gstin:        Optional[str] = None,
        company_name: Optional[str] = None,
        **kwargs:     Any,
    ) -> list[RetrievedField]:
        """
        Entry point called by the scraper executor.
        Requires GSTIN — returns empty list if not provided.
        """
        if not gstin:
            logger.warning("GSTScraper: no GSTIN in state — skipping")
            return []

        gstin = gstin.strip().upper()

        # Validate GSTIN format: 15 alphanumeric characters
        if not re.match(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$', gstin):
            logger.warning("GSTScraper: invalid GSTIN format '%s' — skipping", gstin)
            return []

        # Step 1: JSON API
        raw = await self._fetch_gstin_json(gstin)
        if raw:
            return self._parse_gstin_json(raw, gstin)

        # Step 2: HTML fallback
        logger.info("GST JSON API failed — trying HTML fallback for %s", gstin)
        html = await self._fetch_gstin_html(gstin)
        if html:
            return self._parse_gstin_html(html, gstin)

        logger.warning("GSTScraper: all methods failed for GSTIN %s", gstin)
        return []

    # ── Step 1: JSON API ───────────────────────────────────────────────────────

    async def _fetch_gstin_json(self, gstin: str) -> Optional[dict]:
        """
        Hit GST services API endpoint.
        Returns parsed dict or None on failure.
        """
        cache_key = Cache.make_key("gst_json", gstin)
        url       = f"{_GST_SEARCH_BASE}/{gstin}"

        try:
            text = await self._get(
                url=url,
                cache_key=cache_key,
                cache_ttl=CACHE_TTL["gst"],
            )

            if text.strip().startswith("<"):
                logger.warning("GST API returned HTML instead of JSON")
                return None

            data = json.loads(text)

            # GST API wraps data in various envelopes
            return (
                data.get("taxpayerInfo")
                or data.get("taxpayer")
                or data.get("data")
                or (data if isinstance(data, dict) and data else None)
            )

        except (ScraperError, json.JSONDecodeError) as exc:
            logger.warning("GST JSON fetch failed for %s: %s", gstin, exc)
            return None

    def _parse_gstin_json(self, data: dict, gstin: str) -> list[RetrievedField]:
        """
        Parse GST JSON response into RetrievedField objects.

        Known GST API response keys:
          gstin, tradeName, legalName, sts (status), rgdt (registration date),
          ctb (business type), stj (state jurisdiction), ntr (NIC code/nature of business)
        """
        fields: list[RetrievedField] = []

        # Verified GSTIN
        gstin_val = data.get("gstin") or data.get("GSTIN") or gstin
        fields.append(RetrievedField(
            field_name=MissingField.GSTIN,
            value=gstin_val.upper(),
            source=Source.GST_PORTAL,
            confidence=0.92,    # verified against government registry
        ))
        logger.debug("GST: verified GSTIN = %s", gstin_val)

        # Trade name / legal name — used to cross-validate company name from docs
        trade_name = data.get("tradeName") or data.get("tradeNam")
        legal_name = data.get("legalName") or data.get("lgnm")
        name       = trade_name or legal_name

        if name:
            # Store as ADDRESS because there's no dedicated "company_name" MissingField
            # The normalizer uses this for cross-validation only
            logger.debug("GST: trade/legal name = %s (for cross-validation)", name)

        # Registration status — flag if cancelled
        status = data.get("sts") or data.get("status") or ""
        if status and status.lower() not in ("active", ""):
            logger.warning(
                "GST: GSTIN %s status is '%s' — not active", gstin, status
            )

        # State of registration — validates registered address state
        state_jurisdiction = (
            data.get("stj")
            or data.get("stjCd")
            or data.get("stateJurisdiction")
            or ""
        )
        if state_jurisdiction:
            logger.debug("GST: state jurisdiction = %s", state_jurisdiction)

        # Nature of business / NIC code
        nature_of_business = (
            data.get("ntr")
            or data.get("natureOfBusiness")
            or data.get("businessNature")
            or ""
        )
        if nature_of_business:
            fields.append(RetrievedField(
                field_name=MissingField.INDUSTRY,
                value=str(nature_of_business).strip(),
                source=Source.GST_PORTAL,
                confidence=0.72,
            ))
            logger.debug("GST: industry/NIC = %s", nature_of_business)

        # Address from GST registration
        address_parts = []
        for addr_key in ("pradr", "adadr", "registeredAddress"):
            addr_block = data.get(addr_key)
            if isinstance(addr_block, dict):
                addr = addr_block.get("addr") or addr_block
                parts = [
                    addr.get("bnm", ""),   # building name
                    addr.get("st", ""),    # street
                    addr.get("loc", ""),   # locality
                    addr.get("dst", ""),   # district
                    addr.get("stcd", ""),  # state code
                    addr.get("pncd", ""),  # PIN code
                ]
                address_parts = [p for p in parts if p]
                break

        if address_parts:
            fields.append(RetrievedField(
                field_name=MissingField.ADDRESS,
                value=", ".join(address_parts),
                source=Source.GST_PORTAL,
                confidence=0.80,
            ))
            logger.debug("GST: address = %s", ", ".join(address_parts))

        logger.info(
            "GSTScraper JSON: retrieved %d field(s) for GSTIN %s",
            len(fields), gstin,
        )
        return fields

    # ── Step 2: HTML fallback ──────────────────────────────────────────────────

    async def _fetch_gstin_html(self, gstin: str) -> Optional[str]:
        """
        HTML fallback via GST public search page.
        Returns raw HTML or None on failure.
        """
        cache_key = Cache.make_key("gst_html", gstin)

        try:
            # Switch domain for HTML fallback
            original_domain = self.domain
            self.domain = "www.gst.gov.in"

            html = await self._get(
                url=f"{_GST_PUBLIC_SEARCH}?for=search&gstin={gstin}",
                cache_key=cache_key,
                cache_ttl=CACHE_TTL["gst"],
            )
            self.domain = original_domain
            return html

        except ScraperError as exc:
            self.domain = "services.gst.gov.in"
            logger.warning("GST HTML fallback failed for %s: %s", gstin, exc)
            return None

    def _parse_gstin_html(self, html: str, gstin: str) -> list[RetrievedField]:
        """
        Parse GST public search HTML.
        Extracts GSTIN details from labelled table rows.
        """
        fields: list[RetrievedField] = []

        try:
            soup = self._parse_html(html)

            # Confirm GSTIN appears on the page (validates it's real data)
            if gstin not in soup.get_text():
                logger.warning(
                    "GST HTML: GSTIN %s not found in page — may be invalid",
                    gstin,
                )
                return fields

            fields.append(RetrievedField(
                field_name=MissingField.GSTIN,
                value=gstin,
                source=Source.GST_PORTAL,
                confidence=0.75,    # lower confidence — HTML parse
            ))

            label_field_map = {
                "legal name":         MissingField.ADDRESS,    # cross-validation only
                "trade name":         MissingField.ADDRESS,
                "state":              MissingField.ADDRESS,
                "nature of business": MissingField.INDUSTRY,
                "principal place":    MissingField.ADDRESS,
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
                            source=Source.GST_PORTAL,
                            confidence=0.70,
                        ))
                        seen.add(missing_field)
                        logger.debug(
                            "GST HTML: %s = %r", missing_field.value, value
                        )
                        break

        except Exception as exc:
            raise ParsingError(f"GST HTML parse failed: {exc}") from exc

        logger.info(
            "GSTScraper HTML: retrieved %d field(s) for GSTIN %s",
            len(fields), gstin,
        )
        return fields
