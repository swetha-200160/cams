"""
modules/scraper_executor.py
LangGraph node: scraper_executor

Runs all scrape tasks produced by task_dispatcher in async parallel.
For each task:
  - Skips FLAG_MANUAL tasks (handled by flag_engine)
  - Tries sources left-to-right (primary → fallback)
  - Stops on first successful result for that field
  - Collects all RetrievedField objects into raw_results["_all_fields"]

Concurrency:
  asyncio.gather() runs all tasks concurrently.
  Per-domain rate limiting in BaseScraper prevents flooding.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.state import AgentState
from core.state import ScraperError, SourceUnavailableError
from models.schemas import MissingField, RetrievedField, Source
from scrapers.mca21_scraper import MCA21Scraper
from scrapers.gst_scraper import GSTScraper
from scrapers.ecourts_scraper import ECourtsScraper
from scrapers.zauba_scraper import ZaubaScraper

logger = logging.getLogger(__name__)

# Registry: Source enum → scraper class
_SCRAPER_REGISTRY = {
    Source.MCA21:      MCA21Scraper,
    Source.GST_PORTAL: GSTScraper,
    Source.ECOURTS:    ECourtsScraper,
    Source.ZAUBA:      ZaubaScraper,
}


def _build_scraper_kwargs(state: AgentState) -> dict[str, Any]:
    """Extract borrower identifiers from state for passing to scrapers."""
    overview = state["transformation_input"].tab_data.overview
    return {
        "company_name": overview.company_name,
        "cin":          overview.cin,
        "pan":          overview.pan,
        "gstin":        overview.gstin,
    }


async def _run_task(
    task:            dict[str, Any],
    scraper_kwargs:  dict[str, Any],
) -> list[RetrievedField]:
    """
    Run one scrape task — try sources in order, return on first success.
    Returns empty list on total failure (non-fatal).
    """
    field:   MissingField = task["field"]
    sources: list[Source] = task["sources"]

    # Skip tasks that are solely FLAG_MANUAL
    if sources == [Source.FLAG_MANUAL]:
        return []

    for source in sources:
        if source == Source.FLAG_MANUAL:
            continue

        scraper_cls = _SCRAPER_REGISTRY.get(source)
        if not scraper_cls:
            logger.warning("No scraper registered for source %s", source.value)
            continue

        try:
            async with scraper_cls() as scraper:
                results = await scraper.scrape(**scraper_kwargs)

            # Filter to only fields relevant to this task
            relevant = [r for r in results if r.field_name == field]

            if relevant:
                logger.info(
                    "Scraped %s via %s → %d result(s)",
                    field.value, source.value, len(relevant),
                )
                return relevant

            logger.debug(
                "%s returned no results for field %s — trying fallback",
                source.value, field.value,
            )

        except ScraperError as exc:
            logger.warning(
                "Scraper %s failed for field %s: %s — trying fallback",
                source.value, field.value, exc,
            )
        except SourceUnavailableError as exc:
            logger.warning(
                "Source %s unavailable for field %s: %s — trying fallback",
                source.value, field.value, exc,
            )
        except Exception as exc:
            logger.warning(
                "Unexpected error in scraper %s for field %s: %s",
                source.value, field.value, exc,
            )

    logger.warning("All sources exhausted for field %s", field.value)
    return []


async def _run_all_tasks(
    tasks:           list[dict[str, Any]],
    scraper_kwargs:  dict[str, Any],
) -> list[RetrievedField]:
    """Run all tasks concurrently, collect all RetrievedField results."""
    coroutines = [_run_task(t, scraper_kwargs) for t in tasks]
    results_nested: list[list[RetrievedField]] = await asyncio.gather(*coroutines)
    return [field for sublist in results_nested for field in sublist]


def scraper_executor(state: AgentState) -> dict:
    """
    LangGraph node function (sync wrapper — runs async internally).
    Input  state fields: scrape_tasks, transformation_input
    Output state fields: raw_results, current_step, errors (appended)
    """
    logger.info("=== Scraper Executor: starting ===")
    errors: list[str]       = list(state.get("errors", []))
    tasks:  list[dict]      = state.get("scrape_tasks", [])

    if not tasks:
        logger.warning("Scraper Executor: no tasks to run")
        return {
            "raw_results":  {"_all_fields": []},
            "current_step": "scraper_executor",
            "errors":       errors,
        }

    scraper_kwargs = _build_scraper_kwargs(state)
    scrape_tasks   = [t for t in tasks if t["sources"] != [Source.FLAG_MANUAL]]

    logger.info(
        "Scraper Executor: running %d scrape tasks (skipping %d manual)",
        len(scrape_tasks),
        len(tasks) - len(scrape_tasks),
    )

    try:
        all_fields = asyncio.run(_run_all_tasks(scrape_tasks, scraper_kwargs))
    except Exception as exc:
        msg = f"scraper_executor fatal error: {exc}"
        logger.exception(msg)
        errors.append(msg)
        all_fields = []

    logger.info(
        "Scraper Executor: collected %d RetrievedField objects", len(all_fields)
    )

    return {
        "raw_results":  {"_all_fields": all_fields},
        "current_step": "scraper_executor",
        "errors":       errors,
    }
