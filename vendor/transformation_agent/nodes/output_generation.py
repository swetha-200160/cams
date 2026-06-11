# nodes/output_generation.py
# ──────────────────────────────────────────────────────────────
# NODE 8 — Output Generation
#
# Final node in the LangGraph pipeline. Assembles the complete
# output payload and writes it to disk.
#
# ── OUTPUT FILE STRATEGY ────────────────────────────────────────
# Each pipeline run writes TWO files:
#
#   1. TIMESTAMPED ARCHIVE (never overwritten):
#      output/transformation_output_YYYYMMDD_HHMMSS.json
#      Example: output/transformation_output_20240315_143022.json
#      Preserves every run permanently. Safe for audit / debugging.
#
#   2. FIXED LATEST FILE (overwritten each run):
#      output/transformation_output.json
#      Always contains the most recent run's output.
#      This is the path downstream agents (Agent 2, Agent 3) read from.
#      They always consume the latest result without needing to know
#      the timestamp.
#
# Why both?
#   - Downstream agents expect a fixed, known filename.
#   - Multiple runs (different companies, re-runs after fixes) must
#     not destroy previous outputs.
#   - Timestamped files allow comparison between runs and full audit trail.
#
# ── RUN INDEX ──────────────────────────────────────────────────
# output/run_index.json is maintained alongside the output files.
# It records every run in order:
#   [
#     { "run_id": 1, "timestamp": "...", "status": "success",
#       "filename": "transformation_output_20240315_143022.json",
#       "documents": [...], "error_count": 0 },
#     ...
#   ]
# This lets any downstream system or developer see the full history
# of runs without reading every individual output file.
#
# Status codes:
#   "success"          → no errors, all tabs have data
#   "partial_success"  → errors present but at least one tab has data
#   "failed"           → all tabs are empty
# ──────────────────────────────────────────────────────────────

import json
import os
from datetime import datetime

from config.settings import OUTPUT_FILE, OUTPUT_FOLDER
from state.agent_state import AgentState


# ── Run index path ────────────────────────────────────────────
# Maintained alongside all output files for a full history of runs.
RUN_INDEX_FILE = os.path.join(OUTPUT_FOLDER, "run_index.json")


def output_generation_node(state: AgentState) -> AgentState:
    print("\n" + "─" * 55)
    print("📤  NODE 8 — Output Generation")
    print("─" * 55)

    errors   = list(state.get("errors", []))
    tab_data = state.get("tab_data", {})

    # ── Generate run timestamp ────────────────────────────────
    # Used for both the archive filename and the summary block.
    now           = datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")          # file-safe
    timestamp_iso = now.isoformat()                         # human-readable

    # ── Determine pipeline status ─────────────────────────────
    tabs_populated = {
        k: len(v) if isinstance(v, list) else len(v.keys())
        for k, v in tab_data.items()
        if k in {"overview", "balance_sheet", "income_statement", "cash_flow", "ratio_analysis_data"}
    }
    any_tab_has_data = any(count > 0 for count in tabs_populated.values())

    if not errors and any_tab_has_data:
        status = "success"
    elif any_tab_has_data:
        status = "partial_success"
    else:
        status = "failed"

    # ── Build summary block ───────────────────────────────────
    documents_processed = [
        {
            "filename":       d["filename"],
            "doc_type":       d.get("doc_type", "Unknown"),
            "chars_extracted": len(
                state.get("extracted_texts", {}).get(d["filename"], "")
            ),
            "tables_found":   len(
                state.get("extracted_tables", {}).get(d["filename"], [])
            ),
        }
        for d in state.get("classified_documents", [])
    ]

    summary = {
        "pipeline_status":     status,
        "run_timestamp":       timestamp_iso,
        "total_documents":     len(state.get("classified_documents", [])),
        "documents_processed": documents_processed,
        "tabs_populated":      tabs_populated,
        "error_count":         len(errors),
    }

    # ── Assemble final output payload ─────────────────────────
    # The primary output is company-centric — one consolidated view.
    # overview, balance_sheet, income_statement, cash_flow are each
    # ONE merged dataset across ALL input documents for the company.
    # structured_datasets (per-document raw data) is intentionally
    # EXCLUDED from final_output — it caused per-document output to
    # appear in the JSON, which is the opposite of what CAMS requires.
    # It is written to a separate debug file only.
    final_output = {
        "status":               status,
        "summary":              summary,
        # ── Company-level consolidated output ─────────────────
        "overview":             tab_data.get("overview", {}),
        "balance_sheet":        tab_data.get("balance_sheet", []),
        "income_statement":     tab_data.get("income_statement", []),
        "cash_flow":            tab_data.get("cash_flow", []),
        # ── Ratio analysis data (for Ratio Analysis Agent) ────
        "ratio_analysis_data":  tab_data.get("ratio_analysis_data", []),
        # ── Bank statement transactions (for Bank Statement Agent) ─
        "bank_statements": {
            "transactions": tab_data.get("bank_statements", []),
        },
        # ── GST consolidated data (for GST Analytics Agent) ───
        "gst_data": tab_data.get("gst_data"),
        # ── ITR consolidated data (for Tax Compliance Agent) ──
        "itr_data": tab_data.get("itr_data"),
        "errors":               errors,
    }

    # ── Ensure output folder exists ───────────────────────────
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    # ── Build timestamped archive filename ────────────────────
    # Splits OUTPUT_FILE ("output/transformation_output.json") into
    # base ("transformation_output") and extension (".json") so the
    # timestamp is inserted before the extension.
    base_name      = os.path.splitext(os.path.basename(OUTPUT_FILE))[0]
    archive_name   = f"{base_name}_{timestamp_str}.json"
    archive_path   = os.path.join(OUTPUT_FOLDER, archive_name)

    write_errors = []

    # ── Write 1: Timestamped archive (NEVER overwritten) ──────
    try:
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(final_output, f, indent=2, default=str, ensure_ascii=False)
        print(f"   ✅ Archive written  → {archive_path}")
    except Exception as e:
        err = f"Failed to write archive file: {e}"
        print(f"   ❌ {err}")
        write_errors.append(err)

    # ── Write 2: Fixed latest file (overwritten each run) ─────
    # Downstream agents always read from this known path.
    # Add archive_file field so downstream knows which archive it matches.
    final_output_with_archive = {
        **final_output,
        "archive_file": archive_name,   # tells downstream which archive to use
    }
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(
                final_output_with_archive, f,
                indent=2, default=str, ensure_ascii=False
            )
        print(f"   ✅ Latest file written → {OUTPUT_FILE}")
    except Exception as e:
        err = f"Failed to write latest output file: {e}"
        print(f"   ❌ {err}")
        write_errors.append(err)

    # ── Write 3: Debug file — per-document raw data ─────────
    # structured_datasets is per-document (one FinancialSchema per file).
    # It is written to a SEPARATE debug file so developers can inspect
    # what each individual document contributed, without polluting the
    # primary company-level output that downstream agents consume.
    debug_path = os.path.join(
        OUTPUT_FOLDER,
        f"{base_name}_{timestamp_str}_debug.json"
    )
    try:
        debug_payload = {
            "run_timestamp":      timestamp_iso,
            "structured_datasets": state.get("structured_datasets", {}),
        }
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(debug_payload, f, indent=2, default=str, ensure_ascii=False)
        print(f"   🔍 Debug file written  → {debug_path}")
    except Exception as e:
        print(f"   ⚠️  Could not write debug file: {e}")

    # ── Write 4: Update run index ─────────────────────────────
    _update_run_index(
        run_index_path   = RUN_INDEX_FILE,
        timestamp_iso    = timestamp_iso,
        status           = status,
        archive_name     = archive_name,
        documents        = [d["filename"] for d in documents_processed],
        error_count      = len(errors),
        tabs_populated   = tabs_populated,
    )

    errors.extend(write_errors)

    # ── Terminal summary ──────────────────────────────────────
    print(f"\n   📊 Status              : {status.upper()}")
    print(f"   📄 Documents           : {summary['total_documents']}")
    print(f"   ⚠️  Errors              : {len(errors)}")
    print(f"   📁 Primary output      : {OUTPUT_FILE}")
    print(f"   📁 Archive             : {archive_path}")
    print(f"   🔍 Debug (per-doc)     : {debug_path}")
    print(f"   📋 Run index           : {RUN_INDEX_FILE}")
    print()
    print("   ── Consolidated company output ──")
    print(f"   📌 overview              : {len(final_output.get('overview', {}))} field(s)")
    print(f"   📌 balance_sheet         : {len(final_output.get('balance_sheet', []))} year(s)")
    print(f"   📌 income_statement      : {len(final_output.get('income_statement', []))} year(s)")
    print(f"   📌 cash_flow             : {len(final_output.get('cash_flow', []))} year(s)")
    print(f"   📌 ratio_analysis_data   : {len(final_output.get('ratio_analysis_data', []))} year(s)")
    print(f"   📌 bank_statements       : {len(final_output.get('bank_statements', {}).get('transactions', []))} transaction(s)")
    print(f"   📌 gst_data              : {'present' if final_output.get('gst_data') else 'absent'}")
    print(f"   📌 itr_data              : {'present' if final_output.get('itr_data') else 'absent'}")

    return {
        **state,
        "final_output":  final_output,
        "archive_file":  archive_path,
        "errors":        errors,
        "current_step":  "complete",
    }


# ══════════════════════════════════════════════════════════════
# RUN INDEX MAINTENANCE
# ══════════════════════════════════════════════════════════════

def _update_run_index(
    run_index_path: str,
    timestamp_iso:  str,
    status:         str,
    archive_name:   str,
    documents:      list,
    error_count:    int,
    tabs_populated: dict,
) -> None:
    """
    Append this run's metadata to run_index.json.

    run_index.json structure:
    [
      {
        "run_id":        1,
        "timestamp":     "2024-03-15T14:30:22.123456",
        "status":        "success",
        "archive_file":  "transformation_output_20240315_143022.json",
        "documents":     ["BS_FY23.pdf", "GSTR_Q3.xlsx"],
        "error_count":   0,
        "tabs_populated": {"overview": 4, "balance_sheet": 2, ...}
      },
      ...
    ]

    Creates the file if it doesn't exist.
    Silently logs any failure — run_index is informational, not critical.
    """
    try:
        # Load existing index or start fresh
        if os.path.exists(run_index_path):
            with open(run_index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
            if not isinstance(index, list):
                index = []
        else:
            index = []

        # Next run_id = max existing + 1, or 1 if no history
        next_id = (max((r.get("run_id", 0) for r in index), default=0)) + 1

        index.append({
            "run_id":        next_id,
            "timestamp":     timestamp_iso,
            "status":        status,
            "archive_file":  archive_name,
            "documents":     documents,
            "error_count":   error_count,
            "tabs_populated": tabs_populated,
        })

        with open(run_index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

        print(f"   📋 Run #{next_id} recorded in run_index.json")

    except Exception as e:
        # Run index failure is non-critical — pipeline output is already written
        print(f"   ⚠️  Could not update run_index.json: {e}")