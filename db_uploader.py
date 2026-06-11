"""
db_uploader.py — Seed script: upload an enriched-extraction JSON and its
source documents into PostgreSQL so the run-from-db endpoint can fetch them.

Usage
-----
    python db_uploader.py \\
        --record-id  rec_1 \\
        --json       path/to/enriched_extraction_final.json \\
        --files      path/to/file1.pdf path/to/file2.xlsx ...

The script creates the required tables if they do not exist yet, then
inserts (or replaces) the record and all associated files.

JSON format
-----------
Use enriched_extraction_template.json as the starting point.
All monetary values must be in crores (INR).
Key sections: overview, balance_sheet, income_statement, cash_flow,
bank_statements, gst_data, itr_data, cibil_data, promoter_profiles.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

_DDL = """
CREATE TABLE IF NOT EXISTS cam_source_records (
    record_id   TEXT PRIMARY KEY,
    json_data   JSONB NOT NULL,
    created_at  TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cam_source_files (
    id           SERIAL PRIMARY KEY,
    record_id    TEXT REFERENCES cam_source_records(record_id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    content_b64  TEXT NOT NULL,
    mime_type    TEXT
);
"""


def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", 5432)),
        dbname=os.environ.get("DB_NAME", "aidevdb"),
        user=os.environ.get("DB_USER", "aidev"),
        password=os.environ.get("DB_PASSWORD", ""),
    )


_CRORE = 10_000_000  # 1 crore = 10,000,000 rupees


def _normalize_gst_to_crores(json_data: dict) -> dict:
    """
    Normalize all monetary values inside gst_data from raw rupees to crores.
    The GSTR documents report figures in rupees; the P&L is in crores.
    This prevents a false unit-mismatch discrepancy in the GST analytics agent.
    """
    gst = json_data.get("gst_data")
    if not isinstance(gst, dict):
        return json_data

    import copy
    json_data = copy.deepcopy(json_data)
    gst = json_data["gst_data"]

    # gst_sales
    sales = gst.get("gst_sales") or {}
    if sales.get("annual_taxable_value") is not None:
        sales["annual_taxable_value"] = round(sales["annual_taxable_value"] / _CRORE, 4)
    for entry in sales.get("monthly_taxable_value") or []:
        if entry.get("value") is not None:
            entry["value"] = round(entry["value"] / _CRORE, 4)

    # gst_tax
    tax = gst.get("gst_tax") or {}
    for key in ("igst", "cgst", "sgst", "total_tax_paid"):
        if tax.get(key) is not None:
            tax[key] = round(tax[key] / _CRORE, 4)

    # gst_consistency
    cons = gst.get("gst_consistency") or {}
    for key in ("gstr1_total_sales", "gstr3b_total_sales", "difference"):
        if cons.get(key) is not None:
            cons[key] = round(cons[key] / _CRORE, 4)

    # sales_breakdown
    breakdown = gst.get("sales_breakdown") or {}
    for key in ("b2b_sales", "export_sales", "domestic_sales"):
        if breakdown.get(key) is not None:
            breakdown[key] = round(breakdown[key] / _CRORE, 4)

    # trend_analysis
    trend = gst.get("trend_analysis") or {}
    if trend.get("average_monthly_sales") is not None:
        trend["average_monthly_sales"] = round(trend["average_monthly_sales"] / _CRORE, 4)
    for key in ("highest_month", "lowest_month"):
        entry = trend.get(key) or {}
        if entry.get("value") is not None:
            entry["value"] = round(entry["value"] / _CRORE, 4)

    return json_data


def upload(record_id: str, json_path: Path, file_paths: list[Path]) -> None:
    json_data = json.loads(json_path.read_text(encoding="utf-8"))
    json_data = _normalize_gst_to_crores(json_data)

    with _connect() as conn:
        with conn.cursor() as cur:
            # ensure tables exist
            cur.execute(_DDL)

            # upsert the JSON record
            cur.execute(
                """
                INSERT INTO cam_source_records (record_id, json_data)
                VALUES (%s, %s)
                ON CONFLICT (record_id) DO UPDATE
                    SET json_data  = EXCLUDED.json_data,
                        created_at = now()
                """,
                (record_id, json.dumps(json_data)),
            )

            # replace all existing files for this record
            cur.execute(
                "DELETE FROM cam_source_files WHERE record_id = %s",
                (record_id,),
            )

            for file_path in file_paths:
                file_bytes = file_path.read_bytes()
                content_b64 = base64.b64encode(file_bytes).decode("ascii")
                mime_type, _ = mimetypes.guess_type(file_path.name)
                cur.execute(
                    """
                    INSERT INTO cam_source_files (record_id, filename, content_b64, mime_type)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (record_id, file_path.name, content_b64, mime_type),
                )

        conn.commit()

    print(f"[db_uploader] record_id='{record_id}' uploaded successfully.")
    print(f"  JSON  : {json_path}")
    print(f"  Files : {len(file_paths)} file(s)")
    for fp in file_paths:
        print(f"          {fp.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload CAM source data to PostgreSQL.")
    parser.add_argument("--record-id", required=True, help="Unique record ID, e.g. rec_1")
    parser.add_argument("--json", required=True, type=Path, help="Path to the enriched extraction JSON file")
    parser.add_argument("--files", nargs="*", type=Path, default=[], help="Source document files to attach")
    args = parser.parse_args()

    if not args.json.exists():
        print(f"[db_uploader] ERROR: JSON file not found: {args.json}", file=sys.stderr)
        sys.exit(1)

    missing = [str(f) for f in args.files if not f.exists()]
    if missing:
        print(f"[db_uploader] ERROR: file(s) not found: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    upload(args.record_id, args.json, args.files)


if __name__ == "__main__":
    main()
