
from __future__ import annotations

from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class PipelineFromDbRequest(BaseModel):
    model_config = {"json_schema_extra": {"examples": [
        {
            "record_id": "rec_1",
            "company_name": "Infobeans Technologies Limited",
            "generate_draft": True,
            "use_ai_writer": False,
        }
    ]}}

    record_id: str = Field(
        ...,
        description="Record ID in the database (e.g. 'rec_1'). Must have been seeded via db_uploader.py.",
        examples=["rec_1"],
    )
    company_name: Optional[str] = Field(
        default=None,
        description="Override the company name extracted from the DB record. Leave blank to use the name in the JSON.",
        examples=["Infobeans Technologies Limited"],
    )
    loan_amount: Optional[float] = Field(
        default=None,
        description="Sanctioned loan / facility amount. If omitted, the pipeline will attempt to extract it from the source documents.",
        examples=[150000000],
    )
    industry: Optional[str] = Field(
        default=None,
        description="Industry / sector classification. Overrides the value extracted from the DB record.",
        examples=["Manufacturing"],
    )
    generate_draft: bool = Field(
        default=True,
        description="Set to false to run only analysis without producing a CAM draft.",
    )
    use_ai_writer: bool = Field(
        default=False,
        description="Use Gemini to write rich section content. Requires GEMINI_API_KEY in .env.",
    )


class PipelineRunRequest(BaseModel):
    model_config = {"json_schema_extra": {"examples": [
        {
            "application_id": "abc-enterprises-pvt-ltd-656b8b83",
            "company_name": "ABC Enterprises Pvt Ltd",
            "generate_draft": True,
            "use_ai_writer": False,
        }
    ]}}

    application_id: str = Field(
        ...,
        description="Unique workspace / application identifier returned by POST /api/applications.",
        examples=["abc-enterprises-pvt-ltd-656b8b83"],
    )
    company_name: Optional[str] = Field(
        default=None,
        description="Override the company name stored in the application record. Leave blank to use the stored name.",
        examples=["ABC Enterprises Pvt Ltd"],
    )
    input_documents_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to borrower documents folder. If omitted, the workspace input_docs directory is used automatically.",
    )
    generate_draft: bool = Field(
        default=True,
        description="Set to false to run only transformation/enrichment/analysis without producing a CAM draft.",
    )
    use_ai_writer: bool = Field(
        default=False,
        description="Use Gemini to write rich section content. Requires GEMINI_API_KEY in .env.",
    )


class ArtifactPaths(BaseModel):
    transformation_output: Optional[str] = None
    enrichment_output: Optional[str] = None
    analysis_output: Optional[str] = None
    cam_draft_output: Optional[str] = None
    cam_markdown_output: Optional[str] = None
    cam_docx_output: Optional[str] = None
    cam_pdf_output: Optional[str] = None


class PipelineStageResult(BaseModel):
    stage: Literal["transformation", "enrichment", "analysis", "cam_generation"]
    status: Literal["success", "partial_success", "failed", "skipped"]
    output_path: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    stdout_tail: Optional[str] = None
    stderr_tail: Optional[str] = None


class PipelineRunResponse(BaseModel):
    application_id: str
    company_name: Optional[str] = None
    status: Literal["success", "partial_success", "failed"]
    started_at: datetime
    completed_at: datetime
    workspace_dir: str
    stages: List[PipelineStageResult] = Field(default_factory=list)
    artifacts: ArtifactPaths = Field(default_factory=ArtifactPaths)
    errors: List[str] = Field(default_factory=list)


class FileLocator(BaseModel):
    type: Literal["page", "sheet_cell", "sheet_row", "paragraph", "file", "unknown"] = "unknown"
    page: Optional[int] = None
    sheet_name: Optional[str] = None
    cell: Optional[str] = None
    row_number: Optional[int] = None
    before_row: Optional[Dict[str, Any]] = None
    current_row: Optional[Dict[str, Any]] = None
    after_row: Optional[Dict[str, Any]] = None
    paragraph_index: Optional[int] = None
    label: Optional[str] = None


class EvidenceReference(BaseModel):
    id: str
    document_name: str
    document_path: Optional[str] = None
    hyperlink: Optional[str] = None
    excerpt: Optional[str] = None
    source_field: Optional[str] = None
    source_year: Optional[str] = None
    extracted_value: Any = None
    locator: FileLocator = Field(default_factory=FileLocator)


class CamBlock(BaseModel):
    id: str
    title: str
    text: str
    source_key: Optional[str] = None
    editable: bool = True
    citations: List[EvidenceReference] = Field(default_factory=list)


class CamSection(BaseModel):
    id: str
    title: str
    page_hint: Optional[str] = None
    status: Literal["ready", "partial", "pending"] = "pending"
    summary: Optional[str] = None
    blocks: List[CamBlock] = Field(default_factory=list)


class CamDraft(BaseModel):
    application_id: str
    company_name: str
    generated_at: datetime
    sections: List[CamSection] = Field(default_factory=list)
    source_documents: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class DraftUpdateRequest(BaseModel):
    model_config = {"json_schema_extra": {"examples": [
        {
            "section_id": "financial_analysis",
            "block_id": "revenue_summary",
            "text": "The company reported revenue of ₹ 120 Cr in FY 2025, reflecting a 15% YoY growth.",
        }
    ]}}

    section_id: str = Field(
        ...,
        description="ID of the section containing the block. Obtain from GET /draft response.",
        examples=["financial_analysis"],
    )
    block_id: str = Field(
        ...,
        description="ID of the block to update. Obtain from GET /draft response.",
        examples=["revenue_summary"],
    )
    text: str = Field(
        ...,
        description="New plain-text (Markdown supported) content for the block.",
        examples=["The company reported revenue of ₹ 120 Cr in FY 2025, reflecting a 15% YoY growth."],
    )


class DraftEvidenceUpdateRequest(BaseModel):
    section_id: str
    block_id: str
    citation_id: str
    text: str


class EvidenceConsolePayload(BaseModel):
    application_id: str
    section_id: str
    block_id: str
    citation: EvidenceReference
    editable_text: str
    preview_type: Literal["pdf", "image", "download", "text", "unknown"] = "unknown"
    source_file_url: Optional[str] = None


class AnalysisPayload(BaseModel):
    raw: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _ensure_dict(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {"raw": value}
        return value


class ApplicationCreateRequest(BaseModel):
    model_config = {"json_schema_extra": {"examples": [
        {
            "company_name": "ABC Enterprises Pvt Ltd",
            "loan_amount": 5000000,
            "loan_type": "term_loan",
            "industry": "Manufacturing",
            "application_date": "2026-04-13",
        }
    ]}}

    application_id: Optional[str] = Field(
        default=None,
        description="Leave blank to auto-generate (recommended). Format: <slug>-<8-hex-chars>.",
        examples=["abc-enterprises-pvt-ltd-656b8b83"],
    )
    company_name: str = Field(
        ...,
        description="Legal name of the borrower entity.",
        examples=["ABC Enterprises Pvt Ltd"],
    )
    loan_amount: Optional[float] = Field(
        default=None,
        description="Requested loan amount in the base currency.",
        examples=[5000000],
    )
    application_date: Optional[date] = Field(
        default=None,
        description="Application date in ISO format (YYYY-MM-DD). Defaults to today.",
        examples=["2026-04-13"],
    )
    industry: Optional[str] = Field(
        default=None,
        description="Industry / sector classification for the borrower.",
        examples=["Manufacturing"],
    )
    loan_type: Optional[str] = Field(
        default=None,
        description="Type of credit facility, e.g. term_loan, working_capital, overdraft.",
        examples=["term_loan"],
    )
    status: Optional[str] = Field(
        default=None,
        description="Initial application status. Defaults to 'created' if omitted.",
        examples=["created"],
    )


class ApplicationRecord(BaseModel):
    application_id: str
    company_name: str
    loan_amount: Optional[float] = None
    application_date: Optional[date] = None
    industry: Optional[str] = None
    loan_type: Optional[str] = None
    status: str = "created"
    created_at: datetime
    updated_at: datetime
    document_count: int = 0


class DocumentUploadResponse(BaseModel):
    application_id: str
    uploaded_files: List[str] = Field(default_factory=list)
    stored_in: str
    document_count: int


class GenerateCamRequest(BaseModel):
    company_name: Optional[str] = None


class OrchestratorStatusResponse(BaseModel):
    application_id: str
    company_name: Optional[str] = None
    status: str
    current_stage: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_updated_at: datetime
    workspace_dir: Optional[str] = None
    stages: List[PipelineStageResult] = Field(default_factory=list)
    artifacts: ArtifactPaths = Field(default_factory=ArtifactPaths)
    errors: List[str] = Field(default_factory=list)
    draft_available: bool = False
