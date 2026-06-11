-- ============================================================
-- CAMS — CAM Bypass DB Schema
-- Run this once on your PostgreSQL server to create the tables.
-- ============================================================

-- Records table: stores the enriched extraction JSON per record
CREATE TABLE IF NOT EXISTS cam_source_records (
    record_id   TEXT PRIMARY KEY,          -- e.g. 'rec_1', 'rec_2'
    json_data   JSONB        NOT NULL,     -- full enriched extraction JSON
    created_at  TIMESTAMP    DEFAULT now(),
    updated_at  TIMESTAMP    DEFAULT now()
);

-- Files table: stores source documents (base64) linked to a record
CREATE TABLE IF NOT EXISTS cam_source_files (
    id           SERIAL  PRIMARY KEY,
    record_id    TEXT    NOT NULL REFERENCES cam_source_records(record_id) ON DELETE CASCADE,
    filename     TEXT    NOT NULL,         -- original filename
    content_b64  TEXT    NOT NULL,         -- base64-encoded file content
    mime_type    TEXT                       -- e.g. 'application/pdf', 'image/png'
);

-- Index for fast file lookups by record
CREATE INDEX IF NOT EXISTS idx_cam_source_files_record_id
    ON cam_source_files (record_id);

-- ============================================================
-- Bypass-flow state tables (stateless container support)
-- ============================================================

-- Stores ApplicationRecord for bypass runs (replaces application.json)
CREATE TABLE IF NOT EXISTS cam_applications (
    application_id  TEXT PRIMARY KEY,
    record_json     JSONB        NOT NULL,
    created_at      TIMESTAMP    DEFAULT now(),
    updated_at      TIMESTAMP    DEFAULT now()
);

-- Stores pipeline status for bypass runs (replaces status.json)
CREATE TABLE IF NOT EXISTS cam_pipeline_status (
    application_id  TEXT PRIMARY KEY,
    status_json     JSONB        NOT NULL,
    updated_at      TIMESTAMP    DEFAULT now()
);

-- Stores CAM draft JSON for bypass runs (replaces cam_draft.json)
CREATE TABLE IF NOT EXISTS cam_drafts (
    application_id  TEXT PRIMARY KEY,
    draft_json      JSONB        NOT NULL,
    created_at      TIMESTAMP    DEFAULT now(),
    updated_at      TIMESTAMP    DEFAULT now()
);

-- ============================================================
-- To add a new record manually (without db_uploader.py):
--
--   INSERT INTO cam_source_records (record_id, json_data)
--   VALUES ('rec_2', '{ ... your enriched JSON here ... }');
--
-- To update an existing record's JSON:
--
--   UPDATE cam_source_records
--   SET json_data  = '{ ... updated JSON ... }',
--       updated_at = now()
--   WHERE record_id = 'rec_1';
--
-- To delete a record and all its files:
--
--   DELETE FROM cam_source_records WHERE record_id = 'rec_1';
--
-- To list all records:
--
--   SELECT record_id, created_at, updated_at FROM cam_source_records ORDER BY created_at DESC;
--
-- To list all files for a record:
--
--   SELECT id, filename, mime_type FROM cam_source_files WHERE record_id = 'rec_1';
-- ============================================================
