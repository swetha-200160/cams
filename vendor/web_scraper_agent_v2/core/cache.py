"""
core/cache.py
File-based cache wrapper using diskcache.

No Redis needed — diskcache is file-based, process-safe, and survives restarts.
Singleton pattern: one cache instance shared across all scrapers.

Usage:
    from core.cache import Cache
    Cache.set("mca21:CIN123", html, ttl=604800)
    html = Cache.get("mca21:CIN123")   # None if expired or missing
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import diskcache

from config.settings import CACHE_DIR, CACHE_TTL
from core.state import CacheError

logger = logging.getLogger(__name__)

_cache: Optional[diskcache.Cache] = None


def _get_cache() -> diskcache.Cache:
    global _cache
    if _cache is None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache = diskcache.Cache(str(CACHE_DIR), size_limit=500 * 1024 * 1024)
        logger.debug("Cache initialised at %s", CACHE_DIR)
    return _cache


class Cache:
    """Static interface — no instantiation needed, just import and call."""

    @staticmethod
    def get(key: str) -> Optional[Any]:
        """Return cached value or None if missing / expired."""
        try:
            return _get_cache().get(key)
        except Exception as exc:
            logger.warning("Cache GET failed for '%s': %s", key, exc)
            return None

    @staticmethod
    def set(key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Store value. Falls back to CACHE_TTL['default'] if ttl not given."""
        ttl = ttl or CACHE_TTL.get("default", 86400)
        try:
            _get_cache().set(key, value, expire=ttl)
            logger.debug("Cache SET key='%s' ttl=%ds", key, ttl)
        except Exception as exc:
            logger.warning("Cache SET failed for '%s': %s", key, exc)

    @staticmethod
    def make_key(source: str, identifier: str) -> str:
        """Canonical key builder — consistent across all scrapers."""
        return f"{source}:{identifier}"

    @staticmethod
    def clear() -> None:
        try:
            _get_cache().clear()
            logger.info("Cache cleared")
        except Exception as exc:
            raise CacheError("Failed to clear cache") from exc
