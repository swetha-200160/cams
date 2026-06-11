"""
CAMS Pipeline Diagnostic Script
================================
Run this script to inspect the full state of a pipeline run for any application.
It checks every stage, every output file, every error, and reports exactly what
passed, what failed, and what is missing.

Usage:
    python scripts/check_pipeline.py --app abc-enterprises-pvt-ltd-656b8b83

    # Trigger pipeline + wait + diagnose all in one go:
    python scripts/check_pipeline.py --app abc-enterprises-pvt-ltd-656b8b83 --run

    # Use a custom company name when triggering:
    python scripts/check_pipeline.py --app abc-enterprises-pvt-ltd-656b8b83 --run --company "ABC Enterprises Pvt Ltd"

Optional flags:
    --run        Trigger the pipeline via API, wait for completion, then diagnose
    --company    Company name to use when triggering (default: derived from app id)
    --host       API host (default: http://localhost:8010)
    --workspace  Path to workspaces dir (default: ../workspaces relative to this script)
    --verbose    Show full file contents (not just summaries)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# Project root is one level up from scripts/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from project root before anything else
_env_path = _PROJECT_ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── ANSI colours (Windows-safe) ───────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):    print(f"  {GREEN}[PASS]{RESET} {msg}")
def warn(msg):  print(f"  {YELLOW}[WARN]{RESET} {msg}")
def fail(msg):  print(f"  {RED}[FAIL]{RESET} {msg}")
def info(msg):  print(f"  {CYAN}[INFO]{RESET} {msg}")
def header(msg):print(f"\n{BOLD}{CYAN}{'='*60}{RESET}\n{BOLD}  {msg}{RESET}\n{CYAN}{'='*60}{RESET}")
def section(msg):print(f"\n{BOLD}── {msg} ──{RESET}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"__parse_error__": str(e)}


def file_size_kb(path: Path) -> str:
    try:
        return f"{path.stat().st_size / 1024:.1f} KB"
    except Exception:
        return "unknown size"


def check_json_field(data: dict, field: str, label: str) -> bool:
    val = data.get(field)
    if val is None or val == "" or val == [] or val == {}:
        warn(f"{label} → '{field}' is empty or missing")
        return False
    ok(f"{label} → '{field}': {str(val)[:80]}")
    return True


def count_nonempty(rows: list, key: str) -> int:
    return sum(1 for r in rows if isinstance(r, dict) and r.get(key) not in (None, "", []))


# ── Check 1: Environment ──────────────────────────────────────────────────────

def check_environment(project_root: Path):
    section("Environment & Configuration")

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        ok(f"GROQ_API_KEY is set ({groq_key[:8]}...)")
    else:
        fail("GROQ_API_KEY is NOT set — Agent 1 and Agent 3 will fail without this")

    env_file = project_root / ".env"
    if env_file.exists():
        ok(f".env file found at {env_file}")
    else:
        warn(".env file not found — GROQ_API_KEY must be set in environment manually")

    vendor_root = project_root / "vendor"
    for agent_dir in ["transformation_agent", "web_scraper_agent_v2", "agent3_analysis"]:
        d = vendor_root / agent_dir
        if d.exists():
            ok(f"vendor/{agent_dir} exists")
        else:
            fail(f"vendor/{agent_dir} MISSING — pipeline will crash at that stage")

    for pkg in ["fastapi", "uvicorn", "reportlab", "docx", "openpyxl", "pypdf", "groq"]:
        try:
            __import__(pkg if pkg != "docx" else "docx")
            ok(f"Package '{pkg}' is importable")
        except ImportError:
            fail(f"Package '{pkg}' is NOT installed — run: pip install -r requirements.txt")


# ── Check 2: Workspace & Input Docs ──────────────────────────────────────────

def check_workspace(workspace_root: Path, app_id: str) -> Path:
    section("Workspace & Uploaded Documents")

    app_dir    = workspace_root / app_id
    current    = app_dir / "current"
    input_docs = current / "input_docs"

    if not app_dir.exists():
        fail(f"Application workspace not found: {app_dir}")
        sys.exit(1)
    ok(f"Application workspace: {app_dir}")

    app_json = app_dir / "application.json"
    if app_json.exists():
        data = load_json(app_json)
        ok(f"application.json found — company: {data.get('company_name')}, status: {data.get('status')}")
    else:
        warn("application.json missing — application was not created via POST /api/applications")

    if input_docs.exists():
        files = [f for f in input_docs.rglob("*") if f.is_file()]
        if files:
            ok(f"{len(files)} input document(s) found:")
            for f in sorted(files):
                info(f"  {f.name} ({file_size_kb(f)})")
        else:
            fail("input_docs directory is empty — no documents uploaded")
    else:
        fail(f"input_docs directory does not exist: {input_docs}")

    return current


# ── Check 3: Orchestrator Status ──────────────────────────────────────────────

def check_status(current: Path, app_id: str):
    section("Orchestrator Pipeline Status")

    status_path = current / "status.json"
    if not status_path.exists():
        status_path = current.parent / "orchestrator_status.json"
    if not status_path.exists():
        warn("status.json not found — pipeline may not have been started yet")
        return

    data = load_json(status_path)
    if "__parse_error__" in data:
        fail(f"Could not parse orchestrator_status.json: {data['__parse_error__']}")
        return

    status = data.get("status", "unknown")
    stage  = data.get("current_stage", "none")
    colour = GREEN if status == "success" else (YELLOW if "partial" in status else RED)
    print(f"\n  Overall status : {colour}{BOLD}{status}{RESET}")
    print(f"  Current stage  : {stage}")
    print(f"  Draft available: {data.get('draft_available', False)}")

    if data.get("started_at"):
        print(f"  Started at     : {data['started_at']}")
    if data.get("completed_at"):
        print(f"  Completed at   : {data['completed_at']}")

    errors = data.get("errors", [])
    if errors:
        fail(f"{len(errors)} pipeline error(s):")
        for e in errors:
            print(f"    {RED}→ {e}{RESET}")
    else:
        ok("No pipeline-level errors recorded")

    stages = data.get("stages", [])
    if stages:
        print()
        for st in stages:
            s = st.get("status", "unknown")
            colour = GREEN if s == "success" else (YELLOW if "partial" in s else RED)
            print(f"  Stage: {BOLD}{st.get('stage')}{RESET} → {colour}{s}{RESET}")
            if st.get("stderr_tail"):
                print(f"    {RED}STDERR tail:{RESET}")
                for line in st["stderr_tail"].splitlines()[-10:]:
                    print(f"      {line}")
            if st.get("stdout_tail") and st.get("status") != "success":
                print(f"    STDOUT tail (last 5 lines):")
                for line in st["stdout_tail"].splitlines()[-5:]:
                    print(f"      {line}")
    else:
        warn("No stage results in status file — pipeline may not have completed yet")


# ── Check 4: Agent 1 — Transformation Output ─────────────────────────────────

def check_agent1(outputs: Path):
    section("Agent 1 — Document Extraction (Transformation)")

    path = outputs / "transformation_output.json"
    if not path.exists():
        fail(f"transformation_output.json not found at {outputs}")
        warn("Agent 1 has not run yet, or it failed before writing output")
        return

    ok(f"transformation_output.json found ({file_size_kb(path)})")
    data = load_json(path)

    if "__parse_error__" in data:
        fail(f"File is not valid JSON: {data['__parse_error__']}"); return

    status = data.get("status", "missing")
    colour = GREEN if status in ("success", "partial_success") else RED
    print(f"  Status: {colour}{status}{RESET}")

    errors = data.get("errors", [])
    if errors:
        fail(f"{len(errors)} extraction error(s):")
        for e in (errors[:5] if isinstance(errors, list) else [str(errors)]):
            print(f"    → {e}")

    tab_data = data.get("tab_data") or {}
    overview = tab_data.get("overview") or data.get("overview") or {}
    bs       = tab_data.get("balance_sheet") or data.get("balance_sheet") or []
    is_rows  = tab_data.get("income_statement") or data.get("income_statement") or []
    cf       = tab_data.get("cash_flow") or data.get("cash_flow") or []

    print(f"\n  Tab coverage:")
    print(f"    overview keys      : {len(overview)}")
    print(f"    balance_sheet rows : {len(bs)}")
    print(f"    income_statement   : {len(is_rows)}")
    print(f"    cash_flow rows     : {len(cf)}")

    key_overview = ["company_name", "pan", "cin", "gstin", "industry", "registered_address", "directors"]
    key_bs = ["share_capital", "networth", "total_debt", "current_assets", "current_liabilities"]
    key_is = ["revenue_from_operations", "ebitda", "pat", "finance_cost"]

    missing_overview = [k for k in key_overview if not overview.get(k)]
    if missing_overview:
        warn(f"Missing overview fields: {missing_overview}")
    else:
        ok("All key overview fields present")

    if bs:
        missing_bs = [k for k in key_bs if count_nonempty(bs, k) == 0]
        if missing_bs:
            warn(f"Balance sheet missing fields across all years: {missing_bs}")
        else:
            ok(f"Balance sheet has key fields ({len(bs)} year(s))")

    if is_rows:
        missing_is = [k for k in key_is if count_nonempty(is_rows, k) == 0]
        if missing_is:
            warn(f"Income statement missing fields: {missing_is}")
        else:
            ok(f"Income statement has key fields ({len(is_rows)} year(s))")

    if not bs:   fail("Balance sheet is empty — downstream agents will be impacted")
    if not is_rows: fail("Income statement is empty — downstream agents will be impacted")
    if not cf:   warn("Cash flow is empty — Agent 3 cash_flow_agent will be skipped")

    aux = data.get("auxiliary_data") or {}
    print(f"\n  Auxiliary data:")
    for k in ["bank_statements", "gst_returns", "itr_filings", "roc_filings"]:
        count = len(aux.get(k) or [])
        fn = ok if count > 0 else warn
        fn(f"  {k}: {count} item(s)")


# ── Check 5: Agent 2 — Enrichment Output ─────────────────────────────────────

def check_agent2(outputs: Path):
    section("Agent 2 — Web Enrichment")

    path = outputs / "enrich_output.json"
    if not path.exists():
        fail(f"enrich_output.json not found")
        warn("Agent 2 has not run yet, or it failed before writing output")
        return

    ok(f"enrich_output.json found ({file_size_kb(path)})")
    data = load_json(path)

    if "__parse_error__" in data:
        fail(f"File is not valid JSON: {data['__parse_error__']}"); return

    status = data.get("status", "missing")
    colour = GREEN if status in ("success", "partial_success") else RED
    print(f"  Status: {colour}{status}{RESET}")

    enriched = data.get("enriched_tabs") or {}
    if enriched:
        ok(f"enriched_tabs present — keys: {list(enriched.keys())}")
    else:
        warn("enriched_tabs is empty — CAM generation will fall back to Agent 1 data only")

    errors = data.get("errors", [])
    if errors:
        fail(f"{len(errors)} enrichment error(s):")
        for e in (errors[:5] if isinstance(errors, list) else [str(errors)]):
            print(f"    → {e}")
    else:
        ok("No enrichment errors")

    gaps = data.get("gaps_filled") or data.get("fields_enriched") or []
    if gaps:
        ok(f"Fields enriched: {gaps[:5]}")
    else:
        info("No explicit gap-fill metadata in enrichment output")


# ── Check 6: Agent 3 — Analysis Output ───────────────────────────────────────

def check_agent3(outputs: Path):
    section("Agent 3 — Financial Analysis")

    path = outputs / "analysis_output.json"
    if not path.exists():
        fail("analysis_output.json not found")
        warn("Agent 3 has not run, or failed before writing output")
        return

    ok(f"analysis_output.json found ({file_size_kb(path)})")
    data = load_json(path)

    if "__parse_error__" in data:
        fail(f"File is not valid JSON: {data['__parse_error__']}"); return

    status = data.get("status", "missing")
    colour = GREEN if status in ("success", "partial_success") else RED
    print(f"  Status: {colour}{status}{RESET}")

    executed = data.get("agents_executed") or []
    skipped  = data.get("agents_skipped") or []
    failed   = data.get("agents_failed") or []

    if executed: ok(f"Agents executed ({len(executed)}): {executed}")
    if skipped:  warn(f"Agents skipped  ({len(skipped)}): {skipped}")
    if failed:   fail(f"Agents failed   ({len(failed)}): {failed}")

    sub_agents = [
        "parsed_financials", "ratio_report", "trend_report",
        "banking_behaviour", "cash_flow_projection", "gst_analytics",
        "tax_compliance", "related_party", "industry_intelligence", "market_risk",
    ]
    print(f"\n  Sub-agent output coverage:")
    for key in sub_agents:
        val = data.get(key)
        if not val:
            warn(f"  {key}: no data")
        else:
            sub_status = val.get("status") if isinstance(val, dict) else "present"
            colour = GREEN if sub_status in ("success", "partial_success", "present") else YELLOW
            print(f"  {colour}✓{RESET} {key}: {sub_status}")

    errors = data.get("errors", [])
    if errors:
        fail(f"{len(errors)} analysis error(s):")
        for e in (errors[:5] if isinstance(errors, list) else [str(errors)]):
            print(f"    → {e}")
    else:
        ok("No analysis errors")


# ── Check 7: CAM Draft ────────────────────────────────────────────────────────

def check_cam_draft(cam_dir: Path):
    section("CAM Draft Generation")

    draft_path = cam_dir / "cam_draft.json"
    docx_path  = cam_dir / "cam_draft.docx"
    pdf_path   = cam_dir / "cam_draft.pdf"
    md_path    = cam_dir / "cam_output.md"

    if not draft_path.exists():
        fail("cam_draft.json not found — CAM generation has not completed")
        return

    ok(f"cam_draft.json found ({file_size_kb(draft_path)})")
    data = load_json(draft_path)

    if "__parse_error__" in data:
        fail(f"File is not valid JSON: {data['__parse_error__']}"); return

    company  = data.get("company_name", "unknown")
    sections = data.get("sections", [])
    ok(f"Company: {company}")
    ok(f"Sections: {len(sections)}")

    total_blocks    = 0
    empty_blocks    = 0
    total_citations = 0

    for sec in sections:
        blocks = sec.get("blocks", [])
        total_blocks += len(blocks)
        for b in blocks:
            text = b.get("text", "")
            citations = b.get("citations", [])
            total_citations += len(citations)
            if not text or "not available" in text.lower() or text.strip() == "":
                empty_blocks += 1

    ok(f"Total blocks: {total_blocks}")
    if empty_blocks:
        warn(f"{empty_blocks} block(s) have empty or 'not available' content")
    else:
        ok("All blocks have content")
    ok(f"Evidence citations: {total_citations}")

    print(f"\n  Section coverage:")
    for sec in sections:
        status = sec.get("status", "pending")
        colour = GREEN if status == "ready" else (YELLOW if status == "partial" else RED)
        print(f"  {colour}•{RESET} [{status:8s}] {sec.get('title', sec.get('id'))}")

    print(f"\n  Export files:")
    if docx_path.exists():
        ok(f"cam_draft.docx ({file_size_kb(docx_path)})")
    else:
        warn("cam_draft.docx not generated — call GET /export/docx to create it")

    if pdf_path.exists():
        ok(f"cam_draft.pdf ({file_size_kb(pdf_path)})")
    else:
        warn("cam_draft.pdf not generated — call GET /export/pdf to create it")

    if md_path.exists():
        ok(f"cam_output.md ({file_size_kb(md_path)})")


# ── Check 8: Runtime Agent Dirs ───────────────────────────────────────────────

def check_runtime_dirs(current: Path):
    section("Runtime Agent Execution Directories")

    for agent_dir in ["runtime_agent1", "runtime_agent2"]:
        d = current / agent_dir
        if d.exists():
            ok(f"{agent_dir}/ exists (agent was run)")
            log_dir = d / "logs"
            if log_dir.exists():
                logs = list(log_dir.glob("*.log"))
                if logs:
                    info(f"  Log files: {[l.name for l in logs]}")
            out_dir = d / "outputs"
            if out_dir.exists():
                out_files = list(out_dir.glob("*.json"))
                if out_files:
                    info(f"  Output files: {[f.name for f in out_files]}")
                else:
                    warn(f"  No JSON outputs found in {agent_dir}/outputs/")
        else:
            info(f"{agent_dir}/ not present (agent not yet run or was cleaned up)")


# ── Pipeline Trigger & Poll ───────────────────────────────────────────────────

def api_post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}")


def api_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}")


def trigger_and_wait(host: str, app_id: str, company_name: str):
    section("Triggering Pipeline via API")

    try:
        health = api_get(f"{host}/health")
        ok(f"Server is running — {health.get('service')} v{health.get('version')}")
    except Exception as e:
        fail(f"Cannot reach server at {host} — is uvicorn running?")
        fail(f"  Error: {e}")
        sys.exit(1)

    try:
        resp = api_post(f"{host}/api/orchestrator/run", {
            "application_id": app_id,
            "company_name": company_name,
            "generate_draft": True,
        })
        ok(f"Pipeline queued — status: {resp.get('status')}")
    except Exception as e:
        fail(f"Failed to trigger pipeline: {e}")
        sys.exit(1)

    section("Waiting for Pipeline to Complete")
    terminal_statuses = {"success", "partial_success", "failed"}
    last_stage = None
    elapsed = 0
    poll_interval = 5

    while True:
        time.sleep(poll_interval)
        elapsed += poll_interval
        try:
            status = api_get(f"{host}/api/orchestrator/{app_id}/status")
        except Exception as e:
            warn(f"Poll failed ({elapsed}s): {e}")
            continue

        current_stage  = status.get("current_stage") or "—"
        overall_status = status.get("status", "unknown")
        draft_ready    = status.get("draft_available", False)
        errors         = status.get("errors", [])

        if current_stage != last_stage:
            print(f"\n  [{elapsed:>4}s] Stage: {BOLD}{current_stage}{RESET}  |  Status: {overall_status}")
            last_stage = current_stage

            for st in status.get("stages", []):
                s = st.get("status", "?")
                colour = GREEN if s == "success" else (YELLOW if "partial" in s else RED)
                print(f"         {colour}✓ {st.get('stage')}: {s}{RESET}")
                if st.get("stderr_tail") and s == "failed":
                    for line in st["stderr_tail"].splitlines()[-5:]:
                        print(f"           {RED}{line}{RESET}")
        else:
            print(f"  [{elapsed:>4}s] Still running... stage={current_stage}", end="\r")

        if overall_status in terminal_statuses:
            print()
            colour = GREEN if overall_status == "success" else (YELLOW if "partial" in overall_status else RED)
            print(f"\n  Pipeline finished: {colour}{BOLD}{overall_status}{RESET}")
            if errors:
                fail(f"{len(errors)} error(s):")
                for e in errors:
                    print(f"    {RED}→ {e[:200]}{RESET}")
            if draft_ready:
                ok("Draft is available")
            break

        if elapsed > 600:
            warn("Pipeline has been running for 10 minutes — stopping poll. Run diagnostic manually.")
            break


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if sys.platform == "win32":
        os.system("color")

    parser = argparse.ArgumentParser(description="CAMS Pipeline Diagnostic Tool")
    parser.add_argument("--app",       required=True,                   help="application_id to inspect")
    parser.add_argument("--run",       action="store_true",             help="Trigger pipeline, wait, then diagnose")
    parser.add_argument("--company",   default=None,                    help="Company name (used with --run)")
    parser.add_argument("--host",      default="http://localhost:8010", help="API host (default: http://localhost:8010)")
    parser.add_argument("--workspace", default=None,                    help="Path to workspaces directory")
    parser.add_argument("--verbose",   action="store_true",             help="Show extra detail")
    args = parser.parse_args()

    project_root = _PROJECT_ROOT
    if args.workspace:
        workspace_root = Path(args.workspace) if Path(args.workspace).is_absolute() else Path.cwd() / args.workspace
    else:
        workspace_root = project_root / "workspaces"

    header(f"CAMS Pipeline Diagnostic — {args.app}")
    print(f"  Project root  : {project_root}")
    print(f"  Workspace root: {workspace_root}")
    print(f"  Run at        : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    check_environment(project_root)

    current    = check_workspace(workspace_root, args.app)
    outputs    = current / "outputs"
    cam_dir    = current / "cam"

    if args.run:
        company = args.company or args.app.replace("-", " ").title()
        trigger_and_wait(args.host, args.app, company)

    check_status(current, args.app)
    check_agent1(outputs)
    check_agent2(outputs)
    check_agent3(outputs)
    check_cam_draft(cam_dir)
    check_runtime_dirs(current)

    header("Diagnostic Complete")
    print(f"  {GREEN}[PASS]{RESET} = working correctly")
    print(f"  {YELLOW}[WARN]{RESET} = partial / missing but non-fatal")
    print(f"  {RED}[FAIL]{RESET} = blocking issue that needs fixing")
    print()


if __name__ == "__main__":
    main()
