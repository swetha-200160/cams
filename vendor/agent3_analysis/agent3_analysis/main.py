"""
main.py
FastAPI entry point for Agent 3 — Analysis Agent.

Endpoints:
  POST /analyze   — Accepts Agent 2 output JSON, returns InsightsOutput JSON
  GET  /health    — Health check

Trigger flow:
  Frontend / Agent 2 completion → POST /analyze → LangGraph orchestrator → insights JSON
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv

load_dotenv()
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from agent3_analysis.config import API_HOST, API_PORT, API_TITLE, API_VERSION
from agent3_analysis.orchestrator import run_analysis
from agent3_analysis.schemas.input_schema import Agent2Output
from agent3_analysis.schemas.output_schema import InsightsOutput

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Groq API key — loaded once at startup
# ---------------------------------------------------------------------------
_GROQ_API_KEY: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate required environment variables at startup."""
    global _GROQ_API_KEY
    _GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
    if not _GROQ_API_KEY:
        logger.critical("GROQ_API_KEY environment variable is not set. LLM agents will fail.")
    else:
        logger.info("GROQ_API_KEY loaded. Agent 3 ready.")
    yield
    logger.info("Agent 3 shutting down.")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    description=(
        "Multi-agent financial analysis pipeline for Credit Appraisal Memorandum (CAM) generation. "
        "Accepts Agent 2 (Web Scraper) output and returns structured insights for the Insights tab."
    ),
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
def health_check() -> dict:
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": API_TITLE,
        "version": API_VERSION,
        "groq_key_configured": bool(_GROQ_API_KEY),
    }


@app.post(
    "/analyze",
    response_model=InsightsOutput,
    status_code=status.HTTP_200_OK,
    tags=["Analysis"],
    summary="Run full Agent 3 analysis pipeline",
    description=(
        "Accepts the structured output from Agent 2 (Web Scraper Agent) and runs all 10 "
        "analytical sub-agents in a 3-wave LangGraph pipeline. Returns the merged InsightsOutput "
        "for display in the Insights tab and consumption by the CAM Draft Generator."
    ),
)
def analyze(payload: Agent2Output) -> InsightsOutput:
    """
    Main analysis endpoint.

    Args:
        payload: Validated Agent 2 output JSON.

    Returns:
        InsightsOutput with results from all 10 sub-agents.
    """
    if not _GROQ_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GROQ_API_KEY not configured. Cannot run LLM-based agents.",
        )

    company = (
        payload.enriched_overview.company_name
        if payload.enriched_overview
        else "unknown"
    )
    logger.info("POST /analyze received — company: %s", company)

    try:
        insights = run_analysis(
            input_data=payload,
            groq_api_key=_GROQ_API_KEY,
        )
        logger.info(
            "POST /analyze completed — status: %s | executed: %d | failed: %d",
            insights.status,
            len(insights.agents_executed),
            len(insights.agents_failed),
        )
        return insights

    except Exception as exc:
        logger.error("POST /analyze unhandled exception: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis pipeline failed: {exc}",
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "agent3_analysis.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
        log_level="info",
    )
