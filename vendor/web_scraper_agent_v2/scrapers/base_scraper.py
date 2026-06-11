"""
scrapers/base_scraper.py
Abstract base class for all scrapers.

Every scraper inherits this and gets:
  - Async httpx session with default headers
  - Per-domain token-bucket rate limiter
  - Retry with exponential backoff
  - Cache-check before any network call
  - Structured logging

Subclasses must set:
    source: Source
    domain: str

Subclasses must implement:
    async def scrape(self, **kwargs) -> list[RetrievedField]
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

from core.cache import Cache
from core.state import RateLimitError, ScraperError, SourceUnavailableError
from config.settings import (
    DEFAULT_HEADERS,
    RATE_LIMITS,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_ATTEMPTS,
    RETRY_BACKOFF_BASE,
)
from models.schemas import RetrievedField, Source

logger = logging.getLogger(__name__)


# ── Token bucket (per domain) ─────────────────────────────────────────────────

class _TokenBucket:
    """Simple async token bucket for per-domain rate limiting."""

    def __init__(self, rate: float) -> None:
        self._rate    = rate
        self._tokens  = rate
        self._last_ts = time.monotonic()
        self._lock    = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now           = time.monotonic()
            elapsed       = now - self._last_ts
            self._tokens  = min(self._rate, self._tokens + elapsed * self._rate)
            self._last_ts = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                logger.debug("Rate limit: sleeping %.2fs", wait)
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


_buckets: dict[str, _TokenBucket] = {}


def _get_bucket(domain: str) -> _TokenBucket:
    if domain not in _buckets:
        rate, _ = RATE_LIMITS.get(domain, (1.0, "second"))
        _buckets[domain] = _TokenBucket(rate)
    return _buckets[domain]


# ── Base scraper ──────────────────────────────────────────────────────────────

class BaseScraper(ABC):

    source: Source
    domain: str

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._log = logging.getLogger(self.__class__.__name__)

    async def __aenter__(self) -> "BaseScraper":
        self._client = httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    @abstractmethod
    async def scrape(self, **kwargs: Any) -> list[RetrievedField]:
        """Execute the scrape. Must be implemented by every subclass."""

    # ── Protected helpers ─────────────────────────────────────────────────────

    async def _get(
        self,
        url:       str,
        params:    Optional[dict[str, Any]] = None,
        cache_key: Optional[str] = None,
        cache_ttl: Optional[int] = None,
    ) -> str:
        """GET with cache → rate limit → retry. 403 is non-fatal SourceUnavailableError."""
        if cache_key:
            cached = Cache.get(cache_key)
            if cached is not None:
                self._log.debug("Cache hit: %s", cache_key)
                return cached

        await _get_bucket(self.domain).acquire()

        last_exc: Optional[Exception] = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                self._log.debug("GET %s (attempt %d/%d)", url, attempt, RETRY_ATTEMPTS)
                resp = await self._client.get(url, params=params)

                if resp.status_code == 429:
                    raise RateLimitError(f"HTTP 429 from {url}")
                if resp.status_code == 403:
                    # 403 = portal blocked plain httpx — raise as SourceUnavailable
                    # so scraper can fall back to Playwright without crashing
                    raise SourceUnavailableError(
                        f"HTTP 403 Forbidden — {url} blocks automated requests. "
                        f"Use Playwright fallback for this domain."
                    )
                if resp.status_code >= 500:
                    raise SourceUnavailableError(f"HTTP {resp.status_code} from {url}")

                resp.raise_for_status()
                text = resp.text

                if cache_key:
                    Cache.set(cache_key, text, ttl=cache_ttl)
                return text

            except (RateLimitError, SourceUnavailableError) as exc:
                # Do NOT retry 403 — it won't change. Raise immediately.
                if "403" in str(exc):
                    raise
                last_exc = exc
                wait = RETRY_BACKOFF_BASE ** attempt
                self._log.warning("%s — retry in %.1fs (%d/%d)", exc, wait, attempt, RETRY_ATTEMPTS)
                await asyncio.sleep(wait)

            except httpx.RequestError as exc:
                last_exc = exc
                wait = RETRY_BACKOFF_BASE ** attempt
                self._log.warning("%s — retry in %.1fs (%d/%d)", exc, wait, attempt, RETRY_ATTEMPTS)
                await asyncio.sleep(wait)

        raise ScraperError(f"All {RETRY_ATTEMPTS} attempts failed for {url}") from last_exc

    async def _get_playwright(
        self,
        url:       str,
        cache_key: Optional[str] = None,
        cache_ttl: Optional[int] = None,
    ) -> str:
        """
        Playwright-based GET for portals that block plain httpx (403).
        Renders the page in a real headless Chromium browser.
        Falls back gracefully if Playwright is not installed.
        """
        if cache_key:
            cached = Cache.get(cache_key)
            if cached is not None:
                self._log.debug("Cache hit (playwright): %s", cache_key)
                return cached

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise SourceUnavailableError(
                "Playwright not installed. Run: playwright install chromium"
            )

        from config.settings import PLAYWRIGHT_HEADLESS, PLAYWRIGHT_TIMEOUT_MS

        self._log.debug("Playwright GET %s", url)
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=PLAYWRIGHT_HEADLESS)
                context = await browser.new_context(
                    user_agent=DEFAULT_HEADERS["User-Agent"],
                    locale="en-IN",
                    extra_http_headers={
                        "Accept-Language": DEFAULT_HEADERS["Accept-Language"],
                    },
                )
                page = await context.new_page()
                await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT_MS, wait_until="domcontentloaded")
                text = await page.content()
                await browser.close()

            if cache_key:
                Cache.set(cache_key, text, ttl=cache_ttl)
            return text

        except Exception as exc:
            raise SourceUnavailableError(f"Playwright failed for {url}: {exc}") from exc

    async def _post(
        self,
        url:       str,
        data:      Optional[dict[str, Any]] = None,
        json:      Optional[dict[str, Any]] = None,
        cache_key: Optional[str] = None,
        cache_ttl: Optional[int] = None,
    ) -> str:
        """POST with cache → rate limit → retry."""
        if cache_key:
            cached = Cache.get(cache_key)
            if cached is not None:
                self._log.debug("Cache hit: %s", cache_key)
                return cached

        await _get_bucket(self.domain).acquire()

        last_exc: Optional[Exception] = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                self._log.debug("POST %s (attempt %d/%d)", url, attempt, RETRY_ATTEMPTS)
                resp = await self._client.post(url, data=data, json=json)

                if resp.status_code == 429:
                    raise RateLimitError(f"HTTP 429 from {url}")
                if resp.status_code >= 500:
                    raise SourceUnavailableError(f"HTTP {resp.status_code} from {url}")

                resp.raise_for_status()
                text = resp.text

                if cache_key:
                    Cache.set(cache_key, text, ttl=cache_ttl)
                return text

            except (RateLimitError, SourceUnavailableError, httpx.RequestError) as exc:
                last_exc = exc
                wait = RETRY_BACKOFF_BASE ** attempt
                self._log.warning("%s — retry in %.1fs", exc, wait)
                await asyncio.sleep(wait)

        raise ScraperError(f"All {RETRY_ATTEMPTS} POST attempts failed for {url}") from last_exc

    @staticmethod
    def _parse_html(html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")
