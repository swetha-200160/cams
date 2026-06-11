# state/agent_state.py
# ──────────────────────────────────────────────────────────────
# LangGraph shared state — flows through every node in the pipeline.
# Each node reads fields it needs and writes back its output fields.
# All fields must be initialized in main.py before invoking the agent.
# ──────────────────────────────────────────────────────────────

from typing import TypedDict, List, Dict, Any


class AgentState(TypedDict):

    # ── INPUT ─────────────────────────────────────────────────
    input_folder: str
    # Path to the folder containing all borrower-uploaded documents.
    # Set once in main.py, never modified by any node.

    # ── NODE 1: Document Intake ───────────────────────────────
    document_repository: List[Dict]
    # List of dicts, one per valid document found in input_folder.
    # Each dict: { filename, filepath, extension, doc_type: None }
    # Populated by: document_intake_node
    # Consumed by:  document_identification_node

    # ── NODE 2: Document Identification ──────────────────────
    classified_documents: List[Dict]
    # Same list as document_repository but with doc_type populated.
    # doc_type is one of: Financial Statement | Balance Sheet |
    # Income Statement | Cash Flow Statement | Bank Statement |
    # GST Return | Income Tax Return | ROC Filing | Annual Report | Unknown
    # Populated by: document_identification_node
    # Consumed by:  ocr_extraction_node, table_detection_node, data_cleaning_node

    # ── NODE 3: OCR & Text Extraction ────────────────────────
    extracted_texts: Dict[str, str]
    # { filename: full_markdown_text }
    # Text is in Markdown format (Docling output) for PDFs.
    # Plain text for .docx, .doc, .txt files.
    # Lines joined for image files.
    # Populated by: ocr_extraction_node
    # Consumed by:  data_cleaning_node

    extracted_sections: Dict[str, List]
    # { filename: [ section_string, ... ] }
    # Document split into logical sections (headings + body).
    # Populated by: ocr_extraction_node
    # Consumed by:  (available for downstream use)

    # ── NODE 4: Table Detection ───────────────────────────────
    extracted_tables: Dict[str, List]
    # { filename: [ { headers, rows, num_rows, num_cols, source }, ... ] }
    # Tables parsed from markdown (PDFs) or pandas DataFrames (Excel).
    # Populated by: table_detection_node
    # Consumed by:  data_cleaning_node (for table-rich docs like Excel)

    # ── NODE 5: Data Cleaning & Normalization ─────────────────
    cleaned_data: Dict[str, Any]
    # { filename: { company_name, industry, cin, pan,
    #               years_found, revenue_figures, profit_figures,
    #               asset_figures, notes } }
    # LLM extracts and normalizes raw financial data.
    # Fallback to { raw_text: ... } on JSON parse failure.
    # Populated by: data_cleaning_node
    # Consumed by:  data_structuring_node

    # ── NODE 6: Data Structuring ──────────────────────────────
    structured_datasets: Dict[str, Any]
    # { filename: FinancialSchema.model_dump() }
    # Pydantic-validated structured financial data.
    # Fallback to cleaned_data dict on validation failure.
    # Populated by: data_structuring_node
    # Consumed by:  tab_mapping_node

    # ── NODE 7: Tab Mapping ───────────────────────────────────
    tab_data: Dict[str, Any]
    # {
    #   "overview":          { company_name, industry, cin, pan },
    #   "balance_sheet":     [ { year, total_assets, ..., source_document }, ... ],
    #   "income_statement":  [ { year, revenue, ..., source_document }, ... ],
    #   "cash_flow":         [ { year, operating_cash_flow, ..., source_document }, ... ]
    # }
    # Pure Python — no LLM calls. source_document field enables traceability.
    # Populated by: tab_mapping_node
    # Consumed by:  output_generation_node

    # ── NODE 8: Output Generation ─────────────────────────────
    final_output: Dict[str, Any]
    # Complete pipeline output payload written to transformation_output.json.
    # Consumed by: Web Scraper Agent and Analysis Agent (downstream).
    # Populated by: output_generation_node

    # ── METADATA ──────────────────────────────────────────────
    errors: List[str]
    # Accumulates non-fatal errors from any node.
    # Pipeline continues even when errors are present.
    # Presence of errors sets status to "partial_success" in final output.

    current_step: str
    # Tracks which node is currently executing.
    # Useful for debugging and monitoring pipeline progress.
