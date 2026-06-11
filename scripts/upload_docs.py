"""
upload_docs.py — Reusable document uploader for CAMS backend.

Usage examples:
  # Create application + upload all files from a folder
  python scripts/upload_docs.py --id my-company-001 --name "My Company Pvt Ltd" --docs ./my_docs

  # Upload to an existing application (skip create)
  python scripts/upload_docs.py --id my-company-001 --docs ./my_docs --skip-create

  # Upload from default input_docs folder inside an existing workspace
  python scripts/upload_docs.py --id e2e-test-borrower-993d1744 --skip-create

  # Also run the pipeline after uploading
  python scripts/upload_docs.py --id my-company-001 --name "My Company" --docs ./my_docs --run-pipeline
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "https://demo.canvendor.co.in/cams/docs#/Applications/upload_documents_api_applications__application_id__documents_post"

# Project root is one level up from scripts/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc",
    ".xlsx", ".xls", ".csv",
    ".png", ".jpg", ".jpeg", ".tiff", ".bmp",
}
# ──────────────────────────────────────────────────────────────────────────────


def create_application(application_id: str, company_name: str, **kwargs) -> dict:
    payload = {"application_id": application_id, "company_name": company_name, **kwargs}
    r = requests.post(f"{BASE_URL}/api/applications", json=payload, timeout=30)
    if r.status_code == 409:
        print(f"  [info] Application '{application_id}' already exists — skipping create.")
        return {"application_id": application_id}
    r.raise_for_status()
    data = r.json()
    print(f"  [ok] Application created: {data['application_id']}")
    return data


def upload_documents(application_id: str, docs_dir: Path) -> dict:
    files = [
        f for f in sorted(docs_dir.iterdir())
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not files:
        print(f"  [warn] No supported files found in: {docs_dir}")
        print(f"         Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        return {}

    print(f"  [info] Uploading {len(files)} file(s) from {docs_dir} ...")

    open_files = []
    try:
        for f in files:
            fh = open(f, "rb")
            open_files.append(("files", (f.name, fh, _mime(f.suffix))))
            print(f"         + {f.name}")

        r = requests.post(
            f"{BASE_URL}/api/applications/{application_id}/documents",
            files=open_files,
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        print(f"  [ok] Uploaded {data.get('document_count', len(files))} file(s) → {data.get('stored_in', '')}")
        return data
    finally:
        for _, (_, fh, _) in open_files:
            fh.close()


def run_pipeline(application_id: str, company_name: str | None, use_ai_writer: bool) -> dict:
    payload = {
        "application_id": application_id,
        "generate_draft": True,
        "use_ai_writer": use_ai_writer,
    }
    if company_name:
        payload["company_name"] = company_name

    r = requests.post(f"{BASE_URL}/api/orchestrator/run", json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    print(f"  [ok] Pipeline queued. Poll status at:")
    print(f"       {BASE_URL}/api/orchestrator/{application_id}/status")
    return data


def _mime(ext: str) -> str:
    return {
        ".pdf":  "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc":  "application/msword",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls":  "application/vnd.ms-excel",
        ".csv":  "text/csv",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tiff": "image/tiff",
        ".bmp":  "image/bmp",
    }.get(ext.lower(), "application/octet-stream")


def resolve_docs_dir(application_id: str, docs_arg: str | None) -> Path:
    """Use --docs if given, otherwise fall back to workspace input_docs."""
    if docs_arg:
        p = Path(docs_arg)
        if not p.exists():
            print(f"[error] Docs folder not found: {p}")
            sys.exit(1)
        return p

    default = _PROJECT_ROOT / "workspaces" / application_id / "current" / "input_docs"
    if default.exists():
        print(f"  [info] No --docs given; using workspace folder: {default}")
        return default

    print("[error] No --docs folder provided and no workspace input_docs found.")
    print(f"        Expected: {default}")
    sys.exit(1)


def main() -> None:
    global BASE_URL
    parser = argparse.ArgumentParser(
        description="Upload documents to CAMS and optionally trigger the pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--id",           required=True,  help="Application ID (e.g. my-company-001)")
    parser.add_argument("--name",         default=None,   help="Company name (required when creating)")
    parser.add_argument("--docs",         default=r"C:\Users\abdula\Downloads\transformation_agent 1\transformation_agent\input_docs", help="Path to folder containing documents")
    parser.add_argument("--skip-create",  action="store_true", help="Skip application creation step")
    parser.add_argument("--run-pipeline", action="store_true", help="Trigger pipeline after upload")
    parser.add_argument("--ai-writer",    action="store_true", help="Use Gemini AI writer (needs GEMINI_API_KEY)")
    parser.add_argument("--loan-amount",  type=float, default=None, help="Loan amount (optional)")
    parser.add_argument("--industry",     default=None,   help="Industry (optional)")
    parser.add_argument("--loan-type",    default=None,   help="Loan type (optional)")
    parser.add_argument("--url",          default=BASE_URL, help=f"Base URL (default: {BASE_URL})")
    args = parser.parse_args()

    BASE_URL = args.url.rstrip("/")

    application_id = args.id.strip()
    print(f"\n=== CAMS Upload — Application: {application_id} ===\n")

    if not args.skip_create:
        if not args.name:
            print("[error] --name is required when creating an application (use --skip-create to skip).")
            sys.exit(1)
        extra = {}
        if args.loan_amount: extra["loan_amount"] = args.loan_amount
        if args.industry:    extra["industry"]    = args.industry
        if args.loan_type:   extra["loan_type"]   = args.loan_type
        create_application(application_id, args.name, **extra)
    else:
        print(f"  [skip] Skipping application creation.")

    docs_dir = resolve_docs_dir(application_id, args.docs)
    upload_documents(application_id, docs_dir)

    if args.run_pipeline:
        print("\n  [info] Triggering pipeline ...")
        run_pipeline(application_id, args.name, args.ai_writer)

    print("\n[done]\n")


if __name__ == "__main__":
    main()
