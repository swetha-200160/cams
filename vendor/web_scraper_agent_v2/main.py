"""
main.py
Single entry point for the Web Scraper Agent (Agent 2 — CAMS pipeline).

Usage:
    python main.py                  # full run against live sources
    python main.py --dry-run        # gap detection + dispatch only, no scraping
    python main.py --input path/to/transformation_output.json

What happens when you run this:
    1. Pre-flight  — checks all required packages are installed
    2. Logging     — stdout + logs/web_scraper_agent.log
    3. Load input  — reads dummy_transformation_output.json (or --input path)
    4. Build graph — compiles the LangGraph pipeline
    5. Run         — gap_detector → dispatcher → scrapers → validator
                     → flag_engine → normalizer → tab_writer → analysis_stub
    6. Output      — writes output/enrich_output.json
                   — prints summary table to terminal
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
from pathlib import Path

# ── Path bootstrap — must be the very first thing before any local imports ───
sys.path.insert(0, str(Path(__file__).resolve().parent))


# ── Pre-flight dependency check ───────────────────────────────────────────────

_REQUIRED_PACKAGES = {
    "httpx":        "httpx",
    "bs4":          "beautifulsoup4",
    "lxml":         "lxml",
    "pydantic":     "pydantic",
    "diskcache":    "diskcache",
    "langgraph":    "langgraph",
    "langchain":    "langchain",
    "dotenv":       "python-dotenv",
}


def _preflight() -> None:
    """
    Check all required packages are importable before starting the pipeline.
    Prints a clear install command if anything is missing and exits.
    """
    missing = []
    for module, pkg in _REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(pkg)

    if missing:
        print("\n[ERROR] Missing required packages:")
        for pkg in missing:
            print(f"  • {pkg}")
        print(
            f"\nInstall with:\n"
            f"  pip install {' '.join(missing)}\n"
        )
        sys.exit(1)


# ── Local imports (only after preflight passes) ───────────────────────────────




# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    from config.settings import LOG_DIR, LOG_FILE, LOG_FORMAT, LOG_LEVEL, OUTPUT_DIR

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


# ── Input loading ─────────────────────────────────────────────────────────────

def _load_input(path: Path):
    from models.schemas import TransformationOutput

    logger = logging.getLogger(__name__)

    if not path.exists():
        logger.error("Input file not found: %s", path)
        logger.error(
            "Expected: dummy_transformation_output.json in the project root\n"
            "Or pass a custom path: python main.py --input /path/to/file.json"
        )
        sys.exit(1)

    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    try:
        output = TransformationOutput.model_validate(raw)
        logger.info(
            "Input loaded — status=%-16s | docs=%d | errors=%d | company=%s",
            output.status,
            output.summary.total_documents,
            output.summary.error_count,
            output.tab_data.overview.company_name or "unknown",
        )
        return output
    except Exception as exc:
        logger.exception("Failed to parse input file: %s", exc)
        sys.exit(1)


# ── Dry-run (gap detection only, no live scraping) ────────────────────────────

def _dry_run(transformation_input) -> None:
    """
    Run gap detection and task dispatch only.
    Prints what WOULD be scraped without hitting any live URLs.
    Useful for verifying your input data before a live run.
    """
    from modules.gap_detector import gap_detector
    from modules.task_dispatcher import task_dispatcher
    from models.schemas import Source

    logger = logging.getLogger(__name__)
    logger.info("DRY RUN — no live scraping will occur")

    state = {
        "transformation_input": transformation_input,
        "missing_fields":  [],
        "scrape_tasks":    [],
        "raw_results":     {},
        "retrieved_fields":[],
        "flagged_fields":  [],
        "analysis_output": None,
        "errors":          [],
        "current_step":    "init",
    }

    state.update(gap_detector(state))
    state.update(task_dispatcher(state))

    tasks      = state["scrape_tasks"]
    to_scrape  = [t for t in tasks if t["sources"] != [Source.FLAG_MANUAL]]
    to_flag    = [t for t in tasks if t["sources"] == [Source.FLAG_MANUAL]]

    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  DRY RUN — Web Scraper Agent")
    print(sep)
    print(f"  Company : {transformation_input.tab_data.overview.company_name}")
    print(f"  Gaps    : {len(state['missing_fields'])} fields missing")
    print(f"  Scrape  : {len(to_scrape)} tasks queued")
    print(f"  Manual  : {len(to_flag)} fields flagged for human review\n")

    if to_scrape:
        print("  Fields to scrape:")
        for t in to_scrape:
            sources = " → ".join(s.value for s in t["sources"])
            print(f"    • {t['field'].value:<30} {sources}")

    if to_flag:
        print("\n  Fields requiring manual collection:")
        for t in to_flag:
            print(f"    • {t['field'].value}")

    if state["errors"]:
        print(f"\n  Warnings:")
        for e in state["errors"]:
            print(f"    ⚠  {e}")

    print(f"\n{sep}")
    print("  Run without --dry-run to execute live scraping.")
    print(f"{sep}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CAMS Web Scraper Agent (Agent 2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py                          # live run with dummy data\n"
            "  python main.py --dry-run                # preview gaps, no scraping\n"
            "  python main.py --input /path/to/file    # use real Agent 1 output\n"
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help="Path to transformation_output.json from Agent 1 "
             "(default: dummy_transformation_output.json)",
    )
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Run gap detection and dispatch only — no live scraping",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Path to write enrich_output.json (default: output/enrich_output.json)",
    )
    return parser.parse_args()


def main() -> None:
    # 1. Pre-flight check
    _preflight()

    # 2. Parse CLI args
    args = _parse_args()

    # 3. Logging
    _setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("╔══════════════════════════════════════╗")
    logger.info("║   CAMS — Web Scraper Agent (Agent 2) ║")
    logger.info("╚══════════════════════════════════════╝")

    # 4. Resolve input / output paths
    from config.settings import TRANSFORMATION_OUTPUT_FILE, ENRICHED_OUTPUT_FILE
    input_path = args.input or TRANSFORMATION_OUTPUT_FILE
    output_path = args.output or ENRICHED_OUTPUT_FILE
    logger.info("Input  : %s", input_path)
    logger.info("Output : %s", output_path)

    # 5. Load input
    transformation_input = _load_input(input_path)

    # 6. Dry-run or full run
    if args.dry_run:
        _dry_run(transformation_input)
        return

    # 7. Build and run full pipeline
    from core.graph import build_graph

    graph = build_graph()

    initial_state = {
        "transformation_input": transformation_input,
        "missing_fields":       [],
        "scrape_tasks":         [],
        "raw_results":          {},
        "retrieved_fields":     [],
        "flagged_fields":       [],
        "analysis_output":      None,
        "errors":               [],
        "current_step":         "init",
    }

    logger.info("Pipeline starting...")
    try:
        final_state = graph.invoke(initial_state)
    except Exception as exc:
        logger.exception("Pipeline crashed: %s", exc)
        sys.exit(1)

    # 8. Report non-fatal errors
    errors = final_state.get("errors", [])
    if errors:
        logger.warning("%d non-fatal error(s):", len(errors))
        for err in errors:
            logger.warning("  • %s", err)

    # 9. Copy output to caller-specified path if different from default
    import shutil
    if args.output and args.output.resolve() != ENRICHED_OUTPUT_FILE.resolve():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if ENRICHED_OUTPUT_FILE.exists():
            shutil.copy2(ENRICHED_OUTPUT_FILE, args.output)
            logger.info("Output copied to %s", args.output)

    logger.info("Done — %s", output_path)


if __name__ == "__main__":
    main()
