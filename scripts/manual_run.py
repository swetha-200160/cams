"""
manual_run.py — run and edit the CAMS pipeline manually against a live server.

Sub-commands
------------
  run   (default) — create app, upload docs, run pipeline, print summary
  edit             — list draft sections/blocks, patch one block, export to folder

Usage
-----
  # Start server first:
  uvicorn main:app --host 0.0.0.0 --port 8010

  # Full pipeline run:
  python scripts/manual_run.py run
  python scripts/manual_run.py run --company "Acme Corp" --docs "C:/path/to/docs"
  python scripts/manual_run.py run --app-id existing-id   # skip create+upload

  # Edit a block and export:
  python scripts/manual_run.py edit --app-id manual-test-borrower-3310177b
  python scripts/manual_run.py edit --app-id manual-test-borrower-3310177b --out "C:/my/exports"
  python scripts/manual_run.py edit --app-id manual-test-borrower-3310177b \
      --section sec_1 --block blk_1 --text "New paragraph text here."
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "http://localhost:8010"
DEFAULT_DOCS_DIR = Path(
    r"C:\Users\abdula\Downloads\transformation_agent 1\transformation_agent\input_docs"
)
POLL_INTERVAL_S    = 10
PIPELINE_TIMEOUT_S = 3600
TERMINAL_STATUSES  = {"draft_ready", "analysis_complete", "failed", "partial_success"}


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _check(r: httpx.Response, label: str) -> dict:
    if r.status_code not in (200, 201, 202):
        print(f"[ERROR] {label} — HTTP {r.status_code}")
        print(r.text)
        sys.exit(1)
    return r.json()


def _connect(base_url: str) -> None:
    try:
        with httpx.Client(base_url=base_url, timeout=10) as probe:
            hc = probe.get("/health")
        print(f"Server : {base_url}")
        print(f"Health : {hc.json()}")
    except httpx.ConnectError:
        print(f"[ERROR] Cannot connect to {base_url}. Is the server running?")
        print("        Start it with:  uvicorn main:app --host 0.0.0.0 --port 8010")
        sys.exit(1)


# ── run sub-command ────────────────────────────────────────────────────────────

def create_application(client: httpx.Client, company_name: str) -> str:
    print(f"\n[1/4] Creating application for '{company_name}' ...")
    body = _check(
        client.post("/api/applications", json={
            "company_name": company_name,
            "loan_amount": 50_000_000,
            "industry": "Manufacturing",
            "loan_type": "Term Loan",
        }),
        "create application",
    )
    app_id = body["application_id"]
    print(f"      application_id = {app_id}")
    return app_id


def upload_documents(client: httpx.Client, app_id: str, docs_dir: Path) -> None:
    files = [p for p in docs_dir.iterdir() if p.is_file()]
    if not files:
        print(f"[ERROR] No files found in {docs_dir}")
        sys.exit(1)
    print(f"\n[2/4] Uploading {len(files)} document(s) from {docs_dir} ...")
    multipart = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in files]
    body = _check(client.post(f"/api/applications/{app_id}/documents", files=multipart), "upload")
    print(f"      Stored {body['document_count']} document(s) in {body['stored_in']}")


def run_pipeline(client: httpx.Client, app_id: str, use_ai_writer: bool = False) -> None:
    label = "generate_draft=true, use_ai_writer=true" if use_ai_writer else "generate_draft=true"
    print(f"\n[3/4] Triggering pipeline ({label}) ...")
    _check(client.post("/api/orchestrator/run", json={
        "application_id": app_id,
        "generate_draft": True,
        "use_ai_writer": use_ai_writer,
    }), "run pipeline")
    print(f"      Pipeline queued.")


def poll_until_done(client: httpx.Client, app_id: str) -> dict:
    print(f"\n[4/4] Polling status (timeout={PIPELINE_TIMEOUT_S}s, every {POLL_INTERVAL_S}s) ...")
    deadline = time.time() + PIPELINE_TIMEOUT_S
    while time.time() < deadline:
        body = _check(client.get(f"/api/orchestrator/{app_id}/status"), "poll status")
        status = body.get("status", "")
        stage  = body.get("current_stage", "—")
        print(f"      status={status:<20} current_stage={stage}")
        if status in TERMINAL_STATUSES:
            return body
        time.sleep(POLL_INTERVAL_S)
    print("[ERROR] Timed out waiting for pipeline.")
    sys.exit(1)


def print_summary(status: dict, base_url: str = "") -> None:
    app_id = status["application_id"]
    print("\n" + "─" * 60)
    print(f"  STATUS          : {status['status'].upper()}")
    print(f"  application_id  : {app_id}")
    print(f"  company_name    : {status.get('company_name', '—')}")
    print(f"  draft_available : {status.get('draft_available', False)}")

    stages = {s["stage"]: s["status"] for s in status.get("stages", [])}
    print(f"\n  Stages:")
    for name, s in stages.items():
        print(f"    {name:<20} {s}")

    artifacts = status.get("artifacts") or {}
    print(f"\n  Artifacts:")
    for key, val in artifacts.items():
        tick = "✓ " + Path(val).name if val else "✗  (not produced)"
        print(f"    {key:<25} {tick}")

    errors = status.get("errors") or []
    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for e in errors:
            print(f"    • {e[:120]}")

    print("─" * 60)

    if status.get("draft_available") and base_url:
        print(f"\nDraft ready. Next steps:")
        print(f"  python scripts/manual_run.py edit --app-id {app_id}")
        print(f"  GET  {base_url}/api/orchestrator/{app_id}/draft")


def cmd_run(args: argparse.Namespace) -> None:
    base_url = args.base_url.rstrip("/")
    _connect(base_url)
    with httpx.Client(base_url=base_url, timeout=120) as client:
        if args.app_id:
            app_id = args.app_id
            print(f"\nRe-using existing application_id = {app_id}")
        else:
            docs_dir = Path(args.docs)
            if not docs_dir.exists():
                print(f"[ERROR] Docs directory not found: {docs_dir}")
                sys.exit(1)
            app_id = create_application(client, args.company)
            upload_documents(client, app_id, docs_dir)

        run_pipeline(client, app_id, use_ai_writer=getattr(args, "ai_writer", False))
        final_status = poll_until_done(client, app_id)

    print_summary(final_status, base_url)
    if final_status["status"] == "failed":
        sys.exit(1)


# ── edit sub-command ───────────────────────────────────────────────────────────

def _print_draft_tree(draft: dict) -> None:
    print(f"\n  Company  : {draft.get('company_name', '—')}")
    print(f"  Sections : {len(draft.get('sections', []))}\n")
    for sec in draft.get("sections", []):
        print(f"  [{sec['id']}]  {sec.get('title', '(no title)')}")
        for blk in sec.get("blocks", []):
            preview = (blk.get("text") or "")[:80].replace("\n", " ")
            print(f"      [{blk['id']}]  {preview}{'…' if len(blk.get('text','')) > 80 else ''}")
    print()


def _download_export(client: httpx.Client, app_id: str, fmt: str, out_dir: Path) -> Path | None:
    r = client.get(f"/api/orchestrator/{app_id}/export/{fmt}", timeout=120)
    if r.status_code != 200:
        print(f"  [WARN] {fmt.upper()} export failed ({r.status_code}): {r.text[:120]}")
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"cam_draft.{fmt}"
    dest.write_bytes(r.content)
    print(f"  Saved {fmt.upper():<5} → {dest}  ({len(r.content) // 1024} KB)")
    return dest


def cmd_edit(args: argparse.Namespace) -> None:
    if not args.app_id:
        print("[ERROR] --app-id is required for the edit command.")
        sys.exit(1)

    base_url = args.base_url.rstrip("/")
    app_id   = args.app_id
    out_dir  = Path(args.out) if args.out else Path("exports") / app_id

    _connect(base_url)
    with httpx.Client(base_url=base_url, timeout=120) as client:

        draft = _check(client.get(f"/api/orchestrator/{app_id}/draft"), "fetch draft")
        print("\n── Draft structure ──────────────────────────────────────")
        _print_draft_tree(draft)

        if args.section and args.block and args.text:
            section_id = args.section
            block_id   = args.block
            new_text   = args.text
        else:
            valid_section_ids = {s["id"] for s in draft.get("sections", [])}
            while True:
                section_id = input("  Section ID to edit (e.g. executive_summary): ").strip()
                if section_id in valid_section_ids:
                    break
                print(f"  [ERROR] '{section_id}' not found. Valid IDs: {', '.join(sorted(valid_section_ids))}")

            sec = next(s for s in draft["sections"] if s["id"] == section_id)
            valid_block_ids = {b["id"] for b in sec.get("blocks", [])}
            while True:
                block_id = input("  Block ID to edit   (e.g. blk_1): ").strip()
                if block_id in valid_block_ids:
                    break
                print(f"  [ERROR] '{block_id}' not found. Valid IDs: {', '.join(sorted(valid_block_ids))}")

            blk = next(b for b in sec["blocks"] if b["id"] == block_id)
            current_text = blk.get("text") or ""
            print(f"\n── Current content of [{section_id}] / [{block_id}] ──────────")
            print(current_text)
            print("─" * 60)
            print("  Type your new text below (press Enter twice when done).")
            print("  To keep the current text unchanged, just press Enter twice.\n")

            lines = []
            while True:
                line = input()
                if line == "" and lines and lines[-1] == "":
                    break
                lines.append(line)
            new_text = "\n".join(lines).strip()

            if not new_text:
                new_text = current_text

        if not new_text:
            print("[ERROR] No text provided. Nothing changed.")
            sys.exit(1)

        print(f"\n── Patching [{section_id}] / [{block_id}] ─────────────────────")
        _check(
            client.patch(
                f"/api/orchestrator/{app_id}/draft/block",
                json={"section_id": section_id, "block_id": block_id, "text": new_text},
            ),
            "patch block",
        )
        print(f"  Block updated.")

        updated = _check(client.get(f"/api/orchestrator/{app_id}/draft"), "re-fetch draft")
        sec = next((s for s in updated.get("sections", []) if s["id"] == section_id), None)
        blk = next((b for b in (sec or {}).get("blocks", []) if b["id"] == block_id), None)
        if blk:
            print(f"  Confirmed: \"{(blk['text'] or '')[:80]}\"")

        print(f"\n── Exporting to {out_dir} ──────────────────────────────")
        _download_export(client, app_id, "docx", out_dir)
        _download_export(client, app_id, "pdf",  out_dir)

        out_dir.mkdir(parents=True, exist_ok=True)
        json_dest = out_dir / "cam_draft.json"
        json_dest.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Saved JSON  → {json_dest}")

    print(f"\nDone. Files saved to: {out_dir.resolve()}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CAMS pipeline — manual run & edit tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/manual_run.py run\n"
            "  python scripts/manual_run.py run --app-id my-borrower-abc123\n"
            "  python scripts/manual_run.py edit --app-id my-borrower-abc123\n"
            "  python scripts/manual_run.py edit --app-id my-borrower-abc123 --out C:/exports\n"
            "  python scripts/manual_run.py edit --app-id my-borrower-abc123 \\\n"
            "      --section sec_1 --block blk_1 --text \"Updated paragraph.\"\n"
        ),
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--app-id",  default=None, help="Shortcut: same as 'run --app-id'")

    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="Full pipeline: create → upload → run → report")
    p_run.add_argument("--docs",      type=Path, default=DEFAULT_DOCS_DIR)
    p_run.add_argument("--company",   default="Manual Test Borrower")
    p_run.add_argument("--app-id",    default=None, help="Re-use existing app (skip create+upload)")
    p_run.add_argument("--ai-writer", action="store_true", default=False,
                       help="Use Gemini AI to write rich 40-page CAM content (requires GEMINI_API_KEY)")

    p_edit = sub.add_parser("edit", help="Edit a draft block and export to a folder")
    p_edit.add_argument("--app-id",  required=True)
    p_edit.add_argument("--out",     default=None, help="Output folder (default: exports/<app-id>)")
    p_edit.add_argument("--section", default=None, help="Section ID (skips interactive prompt)")
    p_edit.add_argument("--block",   default=None, help="Block ID (skips interactive prompt)")
    p_edit.add_argument("--text",    default=None, help="New text (skips interactive prompt)")

    args = parser.parse_args()

    if args.cmd == "edit":
        cmd_edit(args)
    else:
        if not hasattr(args, "docs"):
            args.docs    = DEFAULT_DOCS_DIR
        if not hasattr(args, "company"):
            args.company = "Manual Test Borrower"
        cmd_run(args)


if __name__ == "__main__":
    main()
