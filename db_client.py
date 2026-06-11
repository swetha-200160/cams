"""
db_client.py — PostgreSQL helpers for the CAM bypass flow.

Fetches a pre-uploaded enriched-extraction record (JSON + source files)
from cam_source_records / cam_source_files so that Agent 1 and Agent 2
can be skipped entirely.

Table DDL (run once, or let db_uploader.py handle it):

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

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


_BYPASS_DDL = """
CREATE TABLE IF NOT EXISTS cam_applications (
    application_id  TEXT PRIMARY KEY,
    record_json     JSONB        NOT NULL,
    created_at      TIMESTAMP    DEFAULT now(),
    updated_at      TIMESTAMP    DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cam_pipeline_status (
    application_id  TEXT PRIMARY KEY,
    status_json     JSONB        NOT NULL,
    updated_at      TIMESTAMP    DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cam_drafts (
    application_id  TEXT PRIMARY KEY,
    draft_json      JSONB        NOT NULL,
    created_at      TIMESTAMP    DEFAULT now(),
    updated_at      TIMESTAMP    DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cam_analysis (
    application_id  TEXT PRIMARY KEY,
    analysis_json   JSONB        NOT NULL,
    updated_at      TIMESTAMP    DEFAULT now()
);
"""

_tables_ensured = False


def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "192.168.0.43"),
        port=int(os.environ.get("DB_PORT", 6432)),
        dbname=os.environ.get("DB_NAME", "aidevdb"),
        user=os.environ.get("DB_USER", "aidev"),
        password=os.environ.get("DB_PASSWORD", "aidev123"),
    )


def _ensure_tables() -> None:
    global _tables_ensured
    if _tables_ensured:
        return
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_BYPASS_DDL)
        conn.commit()
    _tables_ensured = True


def fetch_record(record_id: str) -> Tuple[Dict[str, Any], List[Tuple[str, bytes]]]:
    """
    Fetch enriched-extraction data for *record_id*.

    Returns
    -------
    json_data : dict
        The enriched extraction JSON stored in cam_source_records.json_data.
    files : list of (filename, file_bytes)
        All source documents stored in cam_source_files, decoded from base64.

    Raises
    ------
    FileNotFoundError
        When no row with the given record_id exists.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT json_data FROM cam_source_records WHERE record_id = %s",
                (record_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise FileNotFoundError(
                    f"No DB record found for record_id='{record_id}'. "
                    "Upload data first with db_uploader.py."
                )
            json_data: Dict[str, Any] = dict(row["json_data"])

            cur.execute(
                "SELECT filename, content_b64 FROM cam_source_files WHERE record_id = %s",
                (record_id,),
            )
            files: List[Tuple[str, bytes]] = [
                (file_row["filename"], base64.b64decode(file_row["content_b64"]))
                for file_row in cur.fetchall()
            ]

    return json_data, files


# ---------------------------------------------------------------------------
# Bypass-flow state helpers — application record, pipeline status, CAM draft
# ---------------------------------------------------------------------------

def save_db_application(application_id: str, record_dict: dict) -> None:
    _ensure_tables()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cam_applications (application_id, record_json, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (application_id) DO UPDATE
                    SET record_json = EXCLUDED.record_json,
                        updated_at  = now()
                """,
                (application_id, json.dumps(record_dict)),
            )
        conn.commit()


def load_db_application(application_id: str) -> dict | None:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT record_json FROM cam_applications WHERE application_id = %s",
                (application_id,),
            )
            row = cur.fetchone()
    return dict(row["record_json"]) if row else None


def find_application_by_record_id(record_id: str) -> dict | None:
    """Return existing cam_applications row whose record_json contains the given record_id."""
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT record_json FROM cam_applications WHERE record_json->>'record_id' = %s LIMIT 1",
                    (record_id,),
                )
                row = cur.fetchone()
        return dict(row["record_json"]) if row else None
    except Exception:
        return None


def list_db_applications() -> list[dict]:
    """Return all application records ordered by most recently updated."""
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT record_json FROM cam_applications ORDER BY updated_at DESC"
                )
                return [dict(row["record_json"]) for row in cur.fetchall()]
    except Exception:
        return []


def save_db_status(application_id: str, status_dict: dict) -> None:
    _ensure_tables()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cam_pipeline_status (application_id, status_json, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (application_id) DO UPDATE
                    SET status_json = EXCLUDED.status_json,
                        updated_at  = now()
                """,
                (application_id, json.dumps(status_dict)),
            )
        conn.commit()


def load_db_status(application_id: str) -> dict | None:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT status_json FROM cam_pipeline_status WHERE application_id = %s",
                (application_id,),
            )
            row = cur.fetchone()
    return dict(row["status_json"]) if row else None


def save_db_draft(application_id: str, draft_dict: dict) -> None:
    _ensure_tables()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cam_drafts (application_id, draft_json, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (application_id) DO UPDATE
                    SET draft_json = EXCLUDED.draft_json,
                        updated_at = now()
                """,
                (application_id, json.dumps(draft_dict)),
            )
        conn.commit()


def load_db_draft(application_id: str) -> dict | None:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT draft_json FROM cam_drafts WHERE application_id = %s",
                (application_id,),
            )
            row = cur.fetchone()
    return dict(row["draft_json"]) if row else None


def save_db_analysis(application_id: str, analysis_dict: dict) -> None:
    _ensure_tables()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cam_analysis (application_id, analysis_json, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (application_id) DO UPDATE
                    SET analysis_json = EXCLUDED.analysis_json,
                        updated_at    = now()
                """,
                (application_id, json.dumps(analysis_dict)),
            )
        conn.commit()


def load_db_analysis(application_id: str) -> dict | None:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT analysis_json FROM cam_analysis WHERE application_id = %s",
                (application_id,),
            )
            row = cur.fetchone()
    return dict(row["analysis_json"]) if row else None


def get_record_id_for_application(application_id: str) -> str | None:
    """Return the record_id stored inside cam_applications.record_json for the given application_id."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT record_json->>'record_id' AS record_id FROM cam_applications WHERE application_id = %s",
                (application_id,),
            )
            row = cur.fetchone()
    return row["record_id"] if row else None


def fetch_file_from_db(record_id: str, filename: str) -> Tuple[bytes, str]:
    """
    Fetch a single file from cam_source_files by record_id and filename (case-insensitive).

    Returns
    -------
    (file_bytes, mime_type)

    Raises
    ------
    FileNotFoundError
        When no matching file exists.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT content_b64, mime_type
                FROM cam_source_files
                WHERE record_id = %s AND lower(filename) = lower(%s)
                LIMIT 1
                """,
                (record_id, filename),
            )
            row = cur.fetchone()

    if row is None:
        raise FileNotFoundError(
            f"File '{filename}' not found in DB for record_id='{record_id}'."
        )

    file_bytes = base64.b64decode(row["content_b64"])
    mime_type = row["mime_type"] or "application/octet-stream"
    return file_bytes, mime_type
