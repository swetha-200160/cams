from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from mappers import map_transformation_output_to_agent2_payload, map_db_json_to_agent3_payload
from export_service import export_draft_to_docx, export_draft_to_pdf
from generation_service import CamDraftGenerator, write_draft_outputs
from models import (
    ApplicationCreateRequest,
    ApplicationRecord,
    ArtifactPaths,
    CamDraft,
    DocumentUploadResponse,
    DraftEvidenceUpdateRequest,
    DraftUpdateRequest,
    EvidenceConsolePayload,
    GenerateCamRequest,
    OrchestratorStatusResponse,
    PipelineFromDbRequest,
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineStageResult,
)
from settings import settings


@dataclass
class WorkspacePaths:
    application_root: Path
    current_root: Path
    input_docs_dir: Path
    outputs_dir: Path
    cam_dir: Path


class UnifiedOrchestratorService:
    def __init__(self) -> None:
        self.vendor_root = settings.vendor_root
        self.workspace_root = settings.workspace_root

    # ------------------------------------------------------------------
    # Application lifecycle
    # ------------------------------------------------------------------
    def create_application(self, payload: ApplicationCreateRequest) -> ApplicationRecord:
        application_id = (payload.application_id or self._generate_application_id(payload.company_name)).strip()
        workspace = self._workspace(application_id)
        application_path = self._application_path(application_id)
        if application_path.exists():
            raise FileExistsError(f"Application '{application_id}' already exists.")

        now = datetime.now(timezone.utc)
        record = ApplicationRecord(
            application_id=application_id,
            company_name=payload.company_name,
            loan_amount=payload.loan_amount,
            application_date=payload.application_date,
            industry=payload.industry,
            loan_type=payload.loan_type,
            status=payload.status or "created",
            created_at=now,
            updated_at=now,
            document_count=self._count_documents(workspace.input_docs_dir),
        )
        self._ensure_workspace_dirs(workspace)
        self._save_application(record)
        self._write_status(
            OrchestratorStatusResponse(
                application_id=application_id,
                company_name=record.company_name,
                status=record.status,
                current_stage=None,
                last_updated_at=now,
                workspace_dir=str(workspace.current_root),
                draft_available=(workspace.cam_dir / "cam_draft.json").exists(),
            )
        )
        return record

    def list_applications(self) -> List[ApplicationRecord]:
        records: List[ApplicationRecord] = []
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        for child in sorted(self.workspace_root.iterdir()):
            if not child.is_dir():
                continue
            application_path = child / "application.json"
            if not application_path.exists():
                continue
            try:
                record = ApplicationRecord.model_validate_json(application_path.read_text(encoding="utf-8"))
                records.append(self._merge_runtime_status(record))
            except Exception:
                continue
        records.sort(key=lambda item: item.updated_at, reverse=True)
        return records

    def get_application(self, application_id: str) -> ApplicationRecord:
        return self._merge_runtime_status(self._load_application(application_id))

    def save_uploaded_documents(self, application_id: str, files: Iterable[tuple[str, bytes]]) -> DocumentUploadResponse:
        workspace = self._workspace(application_id)
        self._ensure_workspace_dirs(workspace)
        record = self._load_application(application_id)

        uploaded_files: List[str] = []
        for original_name, content in files:
            safe_name = Path(original_name).name
            if not safe_name:
                continue
            target = workspace.input_docs_dir / safe_name
            target.write_bytes(content)
            uploaded_files.append(safe_name)

        record.document_count = self._count_documents(workspace.input_docs_dir)
        record.status = "documents_uploaded" if record.document_count else record.status
        record.updated_at = datetime.now(timezone.utc)
        self._save_application(record)
        self._touch_status_from_application(record)

        return DocumentUploadResponse(
            application_id=application_id,
            uploaded_files=uploaded_files,
            stored_in=str(workspace.input_docs_dir),
            document_count=record.document_count,
        )

    # ------------------------------------------------------------------
    # Pipeline lifecycle
    # ------------------------------------------------------------------
    def queue_pipeline(self, request: PipelineRunRequest) -> OrchestratorStatusResponse:
        application_id = request.application_id
        workspace = self._workspace(application_id)
        self._ensure_workspace_dirs(workspace)

        if request.input_documents_dir:
            source_dir = Path(request.input_documents_dir).resolve()
            if not source_dir.exists() or not source_dir.is_dir():
                raise FileNotFoundError(f"Input documents directory does not exist: {source_dir}")

        app_record = self._ensure_application_record(application_id)
        if request.company_name:
            app_record.company_name = request.company_name
        app_record.status = "queued"
        app_record.updated_at = datetime.now(timezone.utc)
        app_record.document_count = self._count_documents(workspace.input_docs_dir)
        self._save_application(app_record)

        queued_status = OrchestratorStatusResponse(
            application_id=application_id,
            company_name=request.company_name or app_record.company_name,
            status="queued",
            current_stage=None,
            started_at=None,
            completed_at=None,
            last_updated_at=app_record.updated_at,
            workspace_dir=str(workspace.current_root),
            stages=[],
            artifacts=self._discover_artifacts(application_id),
            errors=[],
            draft_available=(workspace.cam_dir / "cam_draft.json").exists(),
        )
        self._write_status(queued_status)
        return queued_status

    def run_pipeline(self, request: PipelineRunRequest) -> PipelineRunResponse:
        started_at = datetime.now(timezone.utc)
        stages: List[PipelineStageResult] = []
        errors: List[str] = []
        company_name = request.company_name
        artifacts = ArtifactPaths()

        try:
            workspace = self._prepare_workspace(request.application_id, request.input_documents_dir)
        except Exception as exc:
            errors.append(f"Workspace setup failed: {exc}")
            import logging
            logging.getLogger(__name__).exception("run_pipeline: workspace setup failed for %s", request.application_id)
            # Write a failed status so callers don't see 'queued' forever
            try:
                ws = self._workspace(request.application_id)
                self._write_status(
                    OrchestratorStatusResponse(
                        application_id=request.application_id,
                        company_name=company_name,
                        status="failed",
                        current_stage=None,
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                        last_updated_at=datetime.now(timezone.utc),
                        workspace_dir=str(ws.current_root),
                        stages=stages,
                        artifacts=artifacts,
                        errors=errors,
                        draft_available=False,
                    )
                )
            except Exception:
                pass
            return PipelineRunResponse(
                application_id=request.application_id,
                company_name=company_name,
                status="failed",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                workspace_dir=None,
                stages=stages,
                artifacts=artifacts,
                errors=errors,
            )

        try:
            app_record = self._ensure_application_record(request.application_id)
            if company_name:
                app_record.company_name = company_name
            company_name = company_name or app_record.company_name
            app_record.status = "running"
            app_record.updated_at = started_at
            app_record.document_count = self._count_documents(workspace.input_docs_dir)
            self._save_application(app_record)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("run_pipeline: application record error for %s", request.application_id)
            errors.append(f"Application record error: {exc}")

        self._write_status(
            OrchestratorStatusResponse(
                application_id=request.application_id,
                company_name=company_name,
                status="running",
                current_stage="transformation",
                started_at=started_at,
                completed_at=None,
                last_updated_at=started_at,
                workspace_dir=str(workspace.current_root),
                stages=stages,
                artifacts=artifacts,
                errors=errors,
                draft_available=(workspace.cam_dir / "cam_draft.json").exists(),
            )
        )

        try:
            transformation_result = self._run_agent1(workspace)
            stages.append(transformation_result)
            artifacts.transformation_output = transformation_result.output_path
            self._write_runtime_status(request.application_id, company_name, "running", "enrichment", started_at, stages, artifacts, errors, workspace)
            if transformation_result.status == "failed":
                raise RuntimeError("Transformation stage failed.")

            mapped_path = self._write_agent2_input_adapter(Path(transformation_result.output_path), workspace.outputs_dir)

            enrichment_result = self._run_agent2(workspace, mapped_path)
            stages.append(enrichment_result)
            artifacts.enrichment_output = enrichment_result.output_path
            self._write_runtime_status(request.application_id, company_name, "running", "analysis", started_at, stages, artifacts, errors, workspace)
            if enrichment_result.status == "failed":
                raise RuntimeError("Enrichment stage failed.")

            analysis_result = self._run_agent3(workspace, Path(enrichment_result.output_path))
            stages.append(analysis_result)
            artifacts.analysis_output = analysis_result.output_path
            if analysis_result.status == "failed":
                raise RuntimeError("Analysis stage failed.")

            if request.generate_draft:
                self._write_runtime_status(request.application_id, company_name, "running", "cam_generation", started_at, stages, artifacts, errors, workspace)
                draft_result = self._generate_cam_draft(
                    workspace=workspace,
                    company_name=company_name,
                    transformation_path=Path(transformation_result.output_path),
                    enrichment_path=Path(enrichment_result.output_path),
                    analysis_path=Path(analysis_result.output_path),
                    use_ai_writer=request.use_ai_writer,
                )
                stages.append(draft_result)
                artifacts.cam_draft_output = draft_result.output_path
                details = draft_result.details or {}
                artifacts.cam_markdown_output = details.get("markdown_path")
                artifacts.cam_docx_output = details.get("docx_path")
                artifacts.cam_pdf_output = details.get("pdf_path")

                if not company_name:
                    try:
                        draft_json = json.loads(Path(draft_result.output_path).read_text(encoding="utf-8"))
                        company_name = draft_json.get("company_name")
                    except Exception:
                        pass

        except Exception as exc:
            errors.append(str(exc))

        completed_at = datetime.now(timezone.utc)
        status = self._derive_overall_status(stages, errors)

        response = PipelineRunResponse(
            application_id=request.application_id,
            company_name=company_name,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            workspace_dir=str(workspace.current_root),
            stages=stages,
            artifacts=artifacts,
            errors=errors,
        )
        self._write_status(self._status_from_run_response(response))

        app_record = self._ensure_application_record(request.application_id)
        if company_name:
            app_record.company_name = company_name
        app_record.status = self._application_status_from_run(response)
        app_record.updated_at = completed_at
        app_record.document_count = self._count_documents(workspace.input_docs_dir)
        self._save_application(app_record)
        return response

    def queue_pipeline_from_db(self, request: PipelineFromDbRequest, application_id: str) -> OrchestratorStatusResponse:
        from db_client import save_db_status
        now = datetime.now(timezone.utc)
        queued_status = OrchestratorStatusResponse(
            application_id=application_id,
            company_name=request.company_name,
            status="queued",
            current_stage=None,
            started_at=None,
            completed_at=None,
            last_updated_at=now,
            workspace_dir=None,
            stages=[],
            artifacts=ArtifactPaths(),
            errors=[],
            draft_available=False,
        )
        save_db_status(application_id, queued_status.model_dump(mode="json"))
        return queued_status

    def run_pipeline_from_db(self, request: PipelineFromDbRequest, application_id: str) -> None:
        """
        Bypass flow: fetch enriched JSON + source files from PostgreSQL,
        skip Agent 1 and Agent 2, then run Agent 3 → CAM generation.
        Fully stateless — no filesystem writes. State persists to PostgreSQL.
        """
        import tempfile
        from db_client import fetch_record, save_db_application, save_db_status

        started_at = datetime.now(timezone.utc)
        stages: List[PipelineStageResult] = []
        errors: List[str] = []
        artifacts = ArtifactPaths()

        company_name = request.company_name or application_id
        app_dict = {
            "application_id": application_id,
            "record_id": request.record_id,
            "company_name": company_name,
            "status": "running",
            "created_at": started_at.isoformat(),
            "updated_at": started_at.isoformat(),
            "loan_amount": request.loan_amount,
            "industry": request.industry,
            "description": None,
            "incorporation_date": None,
        }
        save_db_application(application_id, app_dict)

        self._write_db_status(
            application_id, company_name, "running", "db_fetch",
            started_at, stages, artifacts, errors, draft_available=False,
        )

        try:
            # ── Step 1: fetch from DB ────────────────────────────────────────
            db_json, source_files = fetch_record(request.record_id)

            # ── Enrich app_dict with fields extracted from the source JSON ────
            overview = db_json.get("overview") or {}
            app_dict["description"] = overview.get("description")
            app_dict["incorporation_date"] = overview.get("incorporation_date")
            if not app_dict.get("industry"):
                app_dict["industry"] = overview.get("industry")
            if not app_dict.get("company_name") or app_dict["company_name"] == application_id:
                app_dict["company_name"] = overview.get("company_name") or app_dict["company_name"]
                company_name = app_dict["company_name"]
            if not app_dict.get("loan_amount"):
                app_dict["loan_amount"] = (
                    db_json.get("loan_amount")
                    or overview.get("loan_amount")
                    or overview.get("sanctioned_limit")
                )

            # ── Generate description via Groq if not present ─────────────────
            if not app_dict.get("description"):
                try:
                    groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()
                    if groq_api_key:
                        from groq import Groq as _Groq
                        _client = _Groq(api_key=groq_api_key)
                        _resp = _client.chat.completions.create(
                            model=os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant"),
                            messages=[
                                {"role": "system", "content": "You are a financial analyst. Write a concise 2-sentence company description for a credit assessment memo. Be factual and professional. Return only the description text, no extra commentary."},
                                {"role": "user", "content": (
                                    f"Company: {app_dict.get('company_name')}\n"
                                    f"Industry: {app_dict.get('industry')}\n"
                                    f"Net Sales: {overview.get('net_sales')}\n"
                                    f"EBITDA: {overview.get('ebitda')}\n"
                                    f"PAT: {overview.get('pat')}\n"
                                    f"Networth: {overview.get('networth')}\n"
                                    f"Total Debt: {overview.get('total_debt')}"
                                )},
                            ],
                            temperature=0.3,
                            max_tokens=120,
                        )
                        app_dict["description"] = _resp.choices[0].message.content.strip()
                except Exception as _desc_exc:
                    import logging
                    logging.getLogger(__name__).warning("Description generation failed: %s", _desc_exc)

            save_db_application(application_id, app_dict)

            # ── Step 2: build transformation dict in memory ──────────────────
            transformation = db_json
            artifacts.transformation_output = "db://transformation"

            # ── Step 3: build enrichment dict in memory ──────────────────────
            cash_flow_rows = db_json.get("cash_flow", []) or []
            enrichment = {
                "status": db_json.get("status", "success"),
                "enriched_tabs": {
                    "overview": db_json.get("overview", {}),
                    "balance_sheet": db_json.get("balance_sheet", []),
                    "income_statement": db_json.get("income_statement", []),
                    "cash_flow": [
                        {
                            "year": r.get("year"),
                            "operating_cash_flow": r.get("operating_activities"),
                            "investing_cash_flow": r.get("investing_activities"),
                            "financing_cash_flow": r.get("financing_activities"),
                            "net_cash_flow": r.get("net_change_in_cash"),
                            "source_document": r.get("source_document"),
                        }
                        for r in cash_flow_rows
                    ],
                },
                "raw_scraped_data": {
                    "bank_statements": (db_json.get("bank_statements") or {}).get("transactions", []),
                    "gst_data": db_json.get("gst_data"),
                    "itr_data": db_json.get("itr_data"),
                    "roc_filings": [],
                },
                "retrieved_fields": [],
                "flagged_manual": [],
                "summary": db_json.get("summary", {}),
            }
            artifacts.enrichment_output = "db://enrichment"

            stages.append(PipelineStageResult(stage="transformation", status="success", output_path="db://transformation", details={"source": "db", "record_id": request.record_id}))
            stages.append(PipelineStageResult(stage="enrichment", status="success", output_path="db://enrichment", details={"source": "db", "record_id": request.record_id}))

            self._write_db_status(
                application_id, company_name, "running", "analysis",
                started_at, stages, artifacts, errors, draft_available=False,
            )

            # ── Step 4: run Agent 3 in memory ───────────────────────────────
            analysis_result, analysis_dict = self._run_agent3_from_db_memory(db_json)
            stages.append(analysis_result)
            artifacts.analysis_output = "db://analysis"
            from db_client import save_db_analysis
            save_db_analysis(application_id, analysis_dict)
            if analysis_result.status == "failed":
                raise RuntimeError("Analysis stage failed.")

            # ── Step 5: CAM generation using a temp dir for source files ─────
            if request.generate_draft:
                self._write_db_status(
                    application_id, company_name, "running", "cam_generation",
                    started_at, stages, artifacts, errors, draft_available=False,
                )
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
                    tmp_path = Path(tmp_dir)
                    for filename, file_bytes in source_files:
                        (tmp_path / Path(filename).name).write_bytes(file_bytes)
                    draft_result, draft_available = self._generate_cam_draft_from_memory(
                        application_id=application_id,
                        company_name=company_name,
                        transformation=transformation,
                        enrichment=enrichment,
                        analysis=analysis_dict,
                        input_docs_dir=tmp_path,
                        use_ai_writer=request.use_ai_writer,
                    )
                stages.append(draft_result)
                artifacts.cam_draft_output = "db://cam_draft"
            else:
                draft_available = False

        except Exception as exc:
            errors.append(str(exc))
            draft_available = False

        completed_at = datetime.now(timezone.utc)
        status = self._derive_overall_status(stages, errors)

        final_status = OrchestratorStatusResponse(
            application_id=application_id,
            company_name=company_name,
            status=status,
            current_stage=None,
            started_at=started_at,
            completed_at=completed_at,
            last_updated_at=completed_at,
            workspace_dir=None,
            stages=stages,
            artifacts=artifacts,
            errors=errors,
            draft_available=draft_available,
        )
        final_status.stages = [s for s in final_status.stages if s.status not in ("skipped",)]
        save_db_status(application_id, final_status.model_dump(mode="json"))

        app_dict["status"] = status
        app_dict["updated_at"] = completed_at.isoformat()
        save_db_application(application_id, app_dict)

    def _run_agent3_from_db_memory(self, db_json: Dict[str, Any]) -> tuple[PipelineStageResult, Dict[str, Any]]:
        """Run Agent 3 using DB JSON directly. Returns (stage_result, result_dict) — no disk writes."""
        from run_agent3 import run_agent3 as _agent3_run

        groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not groq_api_key:
            raise RuntimeError("GROQ_API_KEY is required for Agent 3 execution.")

        agent3_input = map_db_json_to_agent3_payload(db_json)
        result = _agent3_run(agent3_input, self.vendor_root / "agent3_analysis", groq_api_key, pre_mapped=True)

        status = result.get("status", "failed")
        if status not in {"success", "partial_success", "failed", "skipped"}:
            status = "partial_success"
        stage = PipelineStageResult(
            stage="analysis",
            status=status,
            output_path="db://analysis",
            details={"runner": "agent3_analysis", "source": "db_bypass"},
        )
        return stage, result

    def _generate_cam_draft_from_memory(
        self,
        application_id: str,
        company_name: Optional[str],
        transformation: Dict[str, Any],
        enrichment: Dict[str, Any],
        analysis: Dict[str, Any],
        input_docs_dir: Path,
        use_ai_writer: bool = False,
    ) -> tuple[PipelineStageResult, bool]:
        """Generate CAM draft from in-memory dicts. Saves draft to DB. Returns (stage_result, draft_available)."""
        from db_client import save_db_draft

        generator = CamDraftGenerator(
            application_id=application_id,
            input_docs_dir=input_docs_dir,
        )
        draft = generator.generate(company_name, transformation, enrichment, analysis)

        if use_ai_writer:
            gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
            if gemini_key:
                from generation_service import enrich_with_gemini
                draft = enrich_with_gemini(draft, transformation, enrichment, analysis, gemini_key)

        save_db_draft(application_id, draft.model_dump(mode="json"))

        stage = PipelineStageResult(
            stage="cam_generation",
            status="success",
            output_path="db://cam_draft",
            details={"sections": len(draft.sections), "source": "db_bypass"},
        )
        return stage, True

    def _write_db_status(
        self,
        application_id: str,
        company_name: Optional[str],
        status: str,
        current_stage: Optional[str],
        started_at: datetime,
        stages: List[PipelineStageResult],
        artifacts: ArtifactPaths,
        errors: List[str],
        draft_available: bool,
    ) -> None:
        from db_client import save_db_status
        payload = OrchestratorStatusResponse(
            application_id=application_id,
            company_name=company_name,
            status=status,
            current_stage=current_stage,
            started_at=started_at,
            completed_at=None,
            last_updated_at=datetime.now(timezone.utc),
            workspace_dir=None,
            stages=[s for s in stages if s.status != "skipped"],
            artifacts=artifacts,
            errors=errors,
            draft_available=draft_available,
        )
        save_db_status(application_id, payload.model_dump(mode="json"))

    def get_status(self, application_id: str) -> OrchestratorStatusResponse:
        from db_client import load_db_status
        db_status = load_db_status(application_id)
        if db_status:
            return OrchestratorStatusResponse.model_validate(db_status)

        status_path = self._status_path(application_id)
        if status_path.exists():
            return OrchestratorStatusResponse.model_validate_json(status_path.read_text(encoding="utf-8"))

        workspace = self._workspace(application_id)
        record = self._load_application_optional(application_id)
        draft_available = (workspace.cam_dir / "cam_draft.json").exists()
        now = datetime.now(timezone.utc)
        return OrchestratorStatusResponse(
            application_id=application_id,
            company_name=record.company_name if record else None,
            status=record.status if record else ("draft_ready" if draft_available else "not_started"),
            current_stage=None,
            started_at=None,
            completed_at=None,
            last_updated_at=record.updated_at if record else now,
            workspace_dir=str(workspace.current_root),
            stages=[],
            artifacts=self._discover_artifacts(application_id),
            errors=[],
            draft_available=draft_available,
        )

    def load_draft(self, application_id: str) -> CamDraft:
        from db_client import load_db_draft
        db_draft = load_db_draft(application_id)
        if db_draft:
            return CamDraft.model_validate(db_draft)

        draft_path = self._workspace(application_id).cam_dir / "cam_draft.json"
        if not draft_path.exists():
            raise FileNotFoundError(f"Draft not found for application '{application_id}'.")
        return CamDraft.model_validate_json(draft_path.read_text(encoding="utf-8"))

    def save_draft(self, draft: CamDraft) -> Path:
        from db_client import load_db_status, save_db_draft
        if load_db_status(draft.application_id):
            save_db_draft(draft.application_id, draft.model_dump(mode="json"))
            return Path("db://cam_draft")

        workspace = self._workspace(draft.application_id)
        workspace.cam_dir.mkdir(parents=True, exist_ok=True)
        draft_path = workspace.cam_dir / "cam_draft.json"
        draft_path.write_text(json.dumps(draft.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
        return draft_path

    def generate_cam(self, application_id: str, payload: Optional[GenerateCamRequest] = None) -> Dict[str, Any]:
        workspace = self._workspace(application_id)
        transformation_path = workspace.outputs_dir / "transformation_output.json"
        enrichment_path = workspace.outputs_dir / "enrich_output.json"
        analysis_path = workspace.outputs_dir / "analysis_output.json"
        if not transformation_path.exists() or not enrichment_path.exists() or not analysis_path.exists():
            raise FileNotFoundError("Transformation, enrichment, and analysis outputs must exist before CAM generation.")

        app_record = self._ensure_application_record(application_id)
        company_name = payload.company_name if payload and payload.company_name else app_record.company_name
        started_at = datetime.now(timezone.utc)
        status_snapshot = self.get_status(application_id)
        self._write_status(
            OrchestratorStatusResponse(
                application_id=application_id,
                company_name=company_name,
                status="running",
                current_stage="cam_generation",
                started_at=status_snapshot.started_at or started_at,
                completed_at=None,
                last_updated_at=started_at,
                workspace_dir=status_snapshot.workspace_dir,
                stages=status_snapshot.stages,
                artifacts=status_snapshot.artifacts,
                errors=status_snapshot.errors,
                draft_available=status_snapshot.draft_available,
            )
        )

        result = self._generate_cam_draft(workspace, company_name, transformation_path, enrichment_path, analysis_path)
        completed_at = datetime.now(timezone.utc)

        current_stages = [stage for stage in status_snapshot.stages if stage.stage != "cam_generation"]
        current_stages.append(result)
        artifacts = self._discover_artifacts(application_id)
        status_response = OrchestratorStatusResponse(
            application_id=application_id,
            company_name=company_name,
            status="draft_ready",
            current_stage=None,
            started_at=status_snapshot.started_at or started_at,
            completed_at=completed_at,
            last_updated_at=completed_at,
            workspace_dir=str(workspace.current_root),
            stages=current_stages,
            artifacts=artifacts,
            errors=status_snapshot.errors,
            draft_available=True,
        )
        self._write_status(status_response)

        app_record.company_name = company_name
        app_record.status = "draft_ready"
        app_record.updated_at = completed_at
        self._save_application(app_record)

        return {
            "application_id": application_id,
            "company_name": company_name,
            "status": "success",
            "stage": result.model_dump(mode="json"),
            "artifacts": artifacts.model_dump(mode="json"),
        }

    # ------------------------------------------------------------------
    # Draft and evidence
    # ------------------------------------------------------------------
    def update_block_text(self, application_id: str, update: DraftUpdateRequest) -> CamDraft:
        draft = self.load_draft(application_id)
        for section in draft.sections:
            if section.id != update.section_id:
                continue
            for block in section.blocks:
                if block.id == update.block_id:
                    block.text = update.text
                    section.summary = section.blocks[0].text if section.blocks else section.summary
                    self.save_draft(draft)
                    return draft
        raise KeyError(f"Block {update.block_id} not found in section {update.section_id}.")

    def update_evidence_text(self, application_id: str, update: DraftEvidenceUpdateRequest) -> CamDraft:
        return self.update_block_text(
            application_id,
            DraftUpdateRequest(section_id=update.section_id, block_id=update.block_id, text=update.text),
        )

    def get_evidence_payload(self, application_id: str, section_id: str, block_id: str, citation_id: str) -> EvidenceConsolePayload:
        draft = self.load_draft(application_id)
        for section in draft.sections:
            if section.id != section_id:
                continue
            for block in section.blocks:
                if block.id != block_id:
                    continue
                for citation in block.citations:
                    if citation.id == citation_id:
                        preview_type = self._preview_type(citation.document_name)
                        source_url = f"/api/files/{application_id}/{citation.document_name}" if citation.document_name else None
                        return EvidenceConsolePayload(
                            application_id=application_id,
                            section_id=section_id,
                            block_id=block_id,
                            citation=citation,
                            editable_text=block.text,
                            preview_type=preview_type,
                            source_file_url=source_url,
                        )
        raise KeyError(f"Citation {citation_id} not found.")

    def export_current_docx(self, application_id: str) -> Path:
        draft = self.load_draft(application_id)
        workspace = self._workspace(application_id)
        output_path = workspace.cam_dir / "cam_draft.docx"
        export_draft_to_docx(draft, output_path)
        return output_path

    def export_current_pdf(self, application_id: str) -> Path:
        draft = self.load_draft(application_id)
        workspace = self._workspace(application_id)
        output_path = workspace.cam_dir / "cam_draft.pdf"
        export_draft_to_pdf(draft, output_path)
        return output_path

    def export_current_docx_bytes(self, application_id: str) -> bytes:
        from export_service import export_draft_to_docx_bytes
        draft = self.load_draft(application_id)
        return export_draft_to_docx_bytes(draft)

    def export_current_pdf_bytes(self, application_id: str) -> bytes:
        from export_service import export_draft_to_pdf_bytes
        draft = self.load_draft(application_id)
        return export_draft_to_pdf_bytes(draft)

    def resolve_document_path(self, application_id: str, document_name: str) -> Path:
        workspace = self._workspace(application_id)
        exact = workspace.input_docs_dir / document_name
        if exact.exists():
            return exact
        lowered = document_name.lower()
        for path in workspace.input_docs_dir.rglob("*"):
            if path.is_file() and path.name.lower() == lowered:
                return path
        raise FileNotFoundError(document_name)

    # ------------------------------------------------------------------
    # Frontend tab endpoints
    # ------------------------------------------------------------------
    def get_tabs_bundle(self, application_id: str) -> Dict[str, Any]:
        from db_client import load_db_status, load_db_application, fetch_record
        if load_db_status(application_id):
            app_record = load_db_application(application_id) or {}
            record_id = app_record.get("record_id")
            if not record_id:
                raise FileNotFoundError(f"No record_id found for bypass application '{application_id}'.")
            db_json, _ = fetch_record(record_id)
            cash_flow_rows = db_json.get("cash_flow", []) or []
            enrichment = {
                "enriched_tabs": {
                    "overview": db_json.get("overview", {}),
                    "balance_sheet": db_json.get("balance_sheet", []),
                    "income_statement": db_json.get("income_statement", []),
                    "cash_flow": [
                        {
                            "year": r.get("year"),
                            "operating_cash_flow": r.get("operating_activities"),
                            "investing_cash_flow": r.get("investing_activities"),
                            "financing_cash_flow": r.get("financing_activities"),
                            "net_cash_flow": r.get("net_change_in_cash"),
                        }
                        for r in cash_flow_rows
                    ],
                }
            }
            tabs, source = self._effective_tabs({}, enrichment)
            application = self._load_application_optional(application_id)
            overview_data = tabs.get("overview", {}) or {}
            income_rows = tabs.get("income_statement", []) or []
            latest_income = income_rows[-1] if income_rows else {}
            balance_rows = tabs.get("balance_sheet", []) or []
            years_available = [row.get("year") for row in income_rows or balance_rows if row.get("year")]
            overview = {
                "application_id": application_id,
                "source": source,
                "application": {
                    "company_name": overview_data.get("company_name") or (application.company_name if application else None),
                    "loan_amount": application.loan_amount if application else None,
                    "application_date": application.application_date.isoformat() if application and application.application_date else None,
                    "industry": overview_data.get("industry") or (application.industry if application else None),
                    "loan_type": application.loan_type if application else None,
                    "status": self.get_status(application_id).status,
                },
                "company_profile": {
                    "company_name": overview_data.get("company_name") or (application.company_name if application else None),
                    "description": overview_data.get("description"),
                    "address": overview_data.get("address") or overview_data.get("registered_address"),
                    "pan": overview_data.get("pan"),
                    "gstin": overview_data.get("gstin"),
                    "cin": overview_data.get("cin"),
                    "incorporation_date": overview_data.get("incorporation_date"),
                    "industry": overview_data.get("industry") or (application.industry if application else None),
                    "employees": overview_data.get("employees") or overview_data.get("employee_count"),
                    "net_profit": latest_income.get("pat") or latest_income.get("net_profit"),
                },
                "promoter_details": {"directors": overview_data.get("directors", [])},
                "historical_borrower_info": {
                    "years_available": years_available,
                    "source_documents": [],
                },
                "financial_highlights": {
                    "latest_year": latest_income.get("period_label") or latest_income.get("year"),
                    "revenue": latest_income.get("revenue") or latest_income.get("revenue_from_operations"),
                    "ebitda": latest_income.get("ebitda") or overview_data.get("ebitda"),
                    "pat": latest_income.get("pat") or latest_income.get("net_profit") or overview_data.get("pat"),
                    "current_ratio": None,
                    "debt_to_equity": None,
                },
                "raw": overview_data,
            }
            return {
                "application_id": application_id,
                "overview": overview,
                "balance_sheet": tabs.get("balance_sheet", []),
                "income_statement": tabs.get("income_statement", []),
                "cash_flow": tabs.get("cash_flow", []),
            }

        transformation_path = self._workspace(application_id).outputs_dir / "transformation_output.json"
        if not transformation_path.exists():
            raise FileNotFoundError(f"Transformation output not found for application '{application_id}'. Run the pipeline first.")
        overview = self.get_overview_tab(application_id)
        return {
            "application_id": application_id,
            "overview": overview,
            "balance_sheet": self.get_financial_tab(application_id, "balance_sheet")["rows"],
            "income_statement": self.get_financial_tab(application_id, "income_statement")["rows"],
            "cash_flow": self.get_financial_tab(application_id, "cash_flow")["rows"],
        }

    def get_overview_tab(self, application_id: str) -> Dict[str, Any]:
        application = self._load_application_optional(application_id)
        transformation = self._load_json_optional(self._workspace(application_id).outputs_dir / "transformation_output.json")
        enrichment = self._load_json_optional(self._workspace(application_id).outputs_dir / "enrich_output.json")
        analysis = self._load_json_optional(self._workspace(application_id).outputs_dir / "analysis_output.json")
        tabs, source = self._effective_tabs(transformation, enrichment)
        overview = tabs.get("overview", {}) or {}
        income_rows = tabs.get("income_statement", []) or []
        latest_income = income_rows[-1] if income_rows else {}
        balance_rows = tabs.get("balance_sheet", []) or []
        years_available = [row.get("year") for row in income_rows or balance_rows if row.get("year")]

        return {
            "application_id": application_id,
            "source": source,
            "application": {
                "company_name": overview.get("company_name") or (application.company_name if application else None),
                "loan_amount": application.loan_amount if application else None,
                "application_date": application.application_date.isoformat() if application and application.application_date else None,
                "industry": overview.get("industry") or (application.industry if application else None),
                "loan_type": application.loan_type if application else None,
                "status": self.get_status(application_id).status,
            },
            "company_profile": {
                "company_name": overview.get("company_name") or (application.company_name if application else None),
                "description": overview.get("description"),
                "address": overview.get("address") or overview.get("registered_address"),
                "pan": overview.get("pan"),
                "gstin": overview.get("gstin"),
                "cin": overview.get("cin"),
                "incorporation_date": overview.get("incorporation_date"),
                "industry": overview.get("industry") or (application.industry if application else None),
                "employees": overview.get("employees") or overview.get("employee_count"),
                "net_profit": latest_income.get("pat") or latest_income.get("net_profit"),
            },
            "promoter_details": {
                "directors": overview.get("directors", []),
            },
            "historical_borrower_info": {
                "years_available": years_available,
                "source_documents": transformation.get("summary", {}).get("documents_processed", []) if transformation else [],
            },
            "financial_highlights": {
                "latest_year": latest_income.get("period_label") or latest_income.get("year"),
                "revenue": latest_income.get("revenue") or latest_income.get("revenue_from_operations"),
                "ebitda": latest_income.get("ebitda"),
                "pat": latest_income.get("pat") or latest_income.get("net_profit"),
                "current_ratio": (analysis.get("ratio_report") or {}).get("current_ratio") if analysis else None,
                "debt_to_equity": (analysis.get("ratio_report") or {}).get("debt_to_equity") if analysis else None,
            },
            "raw": overview,
        }

    def get_financial_tab(self, application_id: str, tab_name: str) -> Dict[str, Any]:
        valid_tabs = {"balance_sheet", "income_statement", "cash_flow"}
        if tab_name not in valid_tabs:
            raise KeyError(f"Unsupported tab: {tab_name}")
        transformation = self._load_json_optional(self._workspace(application_id).outputs_dir / "transformation_output.json")
        enrichment = self._load_json_optional(self._workspace(application_id).outputs_dir / "enrich_output.json")
        tabs, source = self._effective_tabs(transformation, enrichment)
        return {
            "application_id": application_id,
            "tab": tab_name,
            "source": source,
            "rows": tabs.get(tab_name, []) or [],
        }

    def get_insights(self, application_id: str) -> Dict[str, Any]:
        from db_client import load_db_analysis
        analysis = load_db_analysis(application_id)
        if analysis is None:
            analysis_path = self._workspace(application_id).outputs_dir / "analysis_output.json"
            if not analysis_path.exists():
                raise FileNotFoundError(f"Insights not found for application '{application_id}'.")
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        sections = [
            "parsed_financials",
            "ratio_report",
            "trend_report",
            "banking_behaviour",
            "cash_flow_projection",
            "gst_analytics",
            "tax_compliance",
            "related_party",
            "industry_intelligence",
            "market_risk",
        ]
        sub_agent_status = {}
        for key in sections:
            payload = analysis.get(key) or {}
            sub_agent_status[key] = {
                "status": payload.get("status"),
                "data_quality": payload.get("data_quality"),
                "citations": len(payload.get("citations", []) or []),
            }
        return {
            "application_id": application_id,
            "company_name": analysis.get("company_name"),
            "status": analysis.get("status"),
            "run_timestamp": analysis.get("run_timestamp"),
            "agents_executed": analysis.get("agents_executed", []),
            "agents_skipped": analysis.get("agents_skipped", []),
            "agents_failed": analysis.get("agents_failed", []),
            "errors": analysis.get("errors", []),
            "sub_agent_status": sub_agent_status,
            "data": analysis,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _prepare_workspace(self, application_id: str, input_documents_dir: Optional[str]) -> WorkspacePaths:
        workspace = self._workspace(application_id)
        self._ensure_workspace_dirs(workspace)

        if input_documents_dir:
            source_dir = Path(input_documents_dir).resolve()
            if not source_dir.exists() or not source_dir.is_dir():
                raise FileNotFoundError(f"Input documents directory does not exist: {source_dir}")
            for child in workspace.input_docs_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            for item in source_dir.iterdir():
                target = workspace.input_docs_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)
        return workspace

    def _workspace(self, application_id: str) -> WorkspacePaths:
        application_root = self.workspace_root / application_id
        current_root = application_root / "current"
        return WorkspacePaths(
            application_root=application_root,
            current_root=current_root,
            input_docs_dir=current_root / "input_docs",
            outputs_dir=current_root / "outputs",
            cam_dir=current_root / "cam",
        )

    def _run_agent1(self, workspace: WorkspacePaths) -> PipelineStageResult:
        import asyncio
        from agent1_patch_source.main import run as _agent1_run

        payload = asyncio.run(_agent1_run(workspace.input_docs_dir))

        normalized_path = workspace.outputs_dir / "transformation_output.json"
        normalized_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        status = payload.get("status", "failed")
        if status not in {"success", "partial_success", "failed", "skipped"}:
            status = "partial_success"
        return PipelineStageResult(
            stage="transformation",
            status=status,
            output_path=str(normalized_path),
            details={"runner": "agent1_patch_source"},
        )

    def _write_agent2_input_adapter(self, transformation_output_path: Path, output_dir: Path) -> Path:
        mapped = map_transformation_output_to_agent2_payload(transformation_output_path)
        mapped_path = output_dir / "transformation_output_for_agent2.json"
        mapped_path.write_text(json.dumps(mapped, indent=2, ensure_ascii=False), encoding="utf-8")
        return mapped_path

    def _run_agent2(self, workspace: WorkspacePaths, mapped_input_path: Path) -> PipelineStageResult:
        agent2_dir = self.vendor_root / "web_scraper_agent_v2"
        target_path = workspace.outputs_dir / "enrich_output.json"

        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env_file = settings.project_root / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env.setdefault(k.strip(), v.strip())

        proc = subprocess.run(
            [sys.executable, "main.py", "--input", str(mapped_input_path), "--output", str(target_path)],
            cwd=str(agent2_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if not target_path.exists():
            return PipelineStageResult(
                stage="enrichment",
                status="failed",
                output_path=str(target_path),
                details={"return_code": proc.returncode},
                stdout_tail=self._tail(proc.stdout),
                stderr_tail=self._tail(proc.stderr),
            )
        status = self._json_status(target_path)
        return PipelineStageResult(
            stage="enrichment",
            status=status,
            output_path=str(target_path),
            details={"return_code": proc.returncode},
            stdout_tail=self._tail(proc.stdout),
            stderr_tail=self._tail(proc.stderr),
        )

    def _run_agent3(self, workspace: WorkspacePaths, enrich_output_path: Path) -> PipelineStageResult:
        from run_agent3 import run_agent3 as _agent3_run

        groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not groq_api_key:
            raise RuntimeError("GROQ_API_KEY is required for Agent 3 execution.")

        output_path = workspace.outputs_dir / "analysis_output.json"
        enrich_dict = json.loads(enrich_output_path.read_text(encoding="utf-8"))
        result = _agent3_run(enrich_dict, self.vendor_root / "agent3_analysis", groq_api_key)

        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        status = result.get("status", "failed")
        if status not in {"success", "partial_success", "failed", "skipped"}:
            status = "partial_success"
        return PipelineStageResult(
            stage="analysis",
            status=status,
            output_path=str(output_path),
            details={"runner": "agent3_analysis"},
        )

    def _generate_cam_draft(
        self,
        workspace: WorkspacePaths,
        company_name: Optional[str],
        transformation_path: Path,
        enrichment_path: Path,
        analysis_path: Path,
        use_ai_writer: bool = False,
    ) -> PipelineStageResult:
        transformation = json.loads(transformation_path.read_text(encoding="utf-8"))
        enrichment = json.loads(enrichment_path.read_text(encoding="utf-8"))
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        generator = CamDraftGenerator(
            application_id=workspace.application_root.name,
            input_docs_dir=workspace.input_docs_dir,
        )
        draft = generator.generate(company_name, transformation, enrichment, analysis)

        if use_ai_writer:
            gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
            if gemini_key:
                from generation_service import enrich_with_gemini
                draft = enrich_with_gemini(draft, transformation, enrichment, analysis, gemini_key)
            else:
                import logging
                logging.getLogger(__name__).warning(
                    "_generate_cam_draft: use_ai_writer=True but GEMINI_API_KEY not set — skipping Gemini enrichment"
                )

        draft_path, markdown_path = write_draft_outputs(draft, workspace.cam_dir)
        docx_path = export_draft_to_docx(draft, workspace.cam_dir / "cam_draft.docx")

        try:
            pdf_path = export_draft_to_pdf(draft, workspace.cam_dir / "cam_draft.pdf")
            pdf_str = str(pdf_path)
        except Exception as _pdf_exc:
            import logging
            logging.getLogger(__name__).warning("PDF export failed: %s", _pdf_exc)
            pdf_str = None

        return PipelineStageResult(
            stage="cam_generation",
            status="success",
            output_path=str(draft_path),
            details={
                "markdown_path": str(markdown_path),
                "docx_path": str(docx_path),
                "pdf_path": pdf_str,
                "sections": len(draft.sections),
            },
        )

    def _application_path(self, application_id: str) -> Path:
        return self.workspace_root / application_id / "application.json"

    def _status_path(self, application_id: str) -> Path:
        return self.workspace_root / application_id / "current" / "status.json"

    def _load_application(self, application_id: str) -> ApplicationRecord:
        path = self._application_path(application_id)
        if not path.exists():
            raise FileNotFoundError(f"Application '{application_id}' not found.")
        return ApplicationRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def _load_application_optional(self, application_id: str) -> Optional[ApplicationRecord]:
        path = self._application_path(application_id)
        if not path.exists():
            return None
        return ApplicationRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def _save_application(self, record: ApplicationRecord) -> Path:
        path = self._application_path(record.application_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def _ensure_application_record(self, application_id: str) -> ApplicationRecord:
        existing = self._load_application_optional(application_id)
        if existing:
            return existing
        now = datetime.now(timezone.utc)
        record = ApplicationRecord(
            application_id=application_id,
            company_name=application_id,
            status="created",
            created_at=now,
            updated_at=now,
            document_count=self._count_documents(self._workspace(application_id).input_docs_dir),
        )
        self._save_application(record)
        return record

    def _merge_runtime_status(self, record: ApplicationRecord) -> ApplicationRecord:
        status_path = self._status_path(record.application_id)
        if status_path.exists():
            try:
                status = OrchestratorStatusResponse.model_validate_json(status_path.read_text(encoding="utf-8"))
                record.status = status.status
                record.updated_at = status.last_updated_at
            except Exception:
                pass
        record.document_count = self._count_documents(self._workspace(record.application_id).input_docs_dir)
        return record

    def _touch_status_from_application(self, record: ApplicationRecord) -> None:
        workspace = self._workspace(record.application_id)
        current = self.get_status(record.application_id)
        self._write_status(
            OrchestratorStatusResponse(
                application_id=record.application_id,
                company_name=record.company_name,
                status=record.status,
                current_stage=current.current_stage,
                started_at=current.started_at,
                completed_at=current.completed_at,
                last_updated_at=record.updated_at,
                workspace_dir=str(workspace.current_root),
                stages=current.stages,
                artifacts=self._discover_artifacts(record.application_id),
                errors=current.errors,
                draft_available=(workspace.cam_dir / "cam_draft.json").exists(),
            )
        )

    def _write_status(self, payload: OrchestratorStatusResponse) -> Path:
        path = self._status_path(payload.application_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def _write_runtime_status(
        self,
        application_id: str,
        company_name: Optional[str],
        status: str,
        current_stage: Optional[str],
        started_at: datetime,
        stages: List[PipelineStageResult],
        artifacts: ArtifactPaths,
        errors: List[str],
        workspace: WorkspacePaths,
    ) -> None:
        self._write_status(
            OrchestratorStatusResponse(
                application_id=application_id,
                company_name=company_name,
                status=status,
                current_stage=current_stage,
                started_at=started_at,
                completed_at=None,
                last_updated_at=datetime.now(timezone.utc),
                workspace_dir=str(workspace.current_root),
                stages=stages,
                artifacts=artifacts,
                errors=errors,
                draft_available=(workspace.cam_dir / "cam_draft.json").exists(),
            )
        )

    def _status_from_run_response(self, response: PipelineRunResponse) -> OrchestratorStatusResponse:
        workspace = self._workspace(response.application_id)
        return OrchestratorStatusResponse(
            application_id=response.application_id,
            company_name=response.company_name,
            status=self._application_status_from_run(response),
            current_stage=None,
            started_at=response.started_at,
            completed_at=response.completed_at,
            last_updated_at=response.completed_at,
            workspace_dir=response.workspace_dir,
            stages=response.stages,
            artifacts=response.artifacts,
            errors=response.errors,
            draft_available=(workspace.cam_dir / "cam_draft.json").exists(),
        )

    def _discover_artifacts(self, application_id: str) -> ArtifactPaths:
        workspace = self._workspace(application_id)
        artifacts = ArtifactPaths()
        paths = {
            "transformation_output": workspace.outputs_dir / "transformation_output.json",
            "enrichment_output": workspace.outputs_dir / "enrich_output.json",
            "analysis_output": workspace.outputs_dir / "analysis_output.json",
            "cam_draft_output": workspace.cam_dir / "cam_draft.json",
            "cam_markdown_output": workspace.cam_dir / "cam_draft.md",
            "cam_docx_output": workspace.cam_dir / "cam_draft.docx",
            "cam_pdf_output": workspace.cam_dir / "cam_draft.pdf",
        }
        for field_name, path in paths.items():
            if path.exists():
                setattr(artifacts, field_name, str(path))
        return artifacts

    @staticmethod
    def _application_status_from_run(response: PipelineRunResponse) -> str:
        if response.artifacts.cam_draft_output and response.status in ("success", "partial_success"):
            return "draft_ready"
        if response.status == "success":
            return "analysis_complete"
        if response.status == "partial_success":
            return "partial_success"
        return "failed"

    @staticmethod
    def _effective_tabs(transformation: Dict[str, Any], enrichment: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
        enriched_tabs = enrichment.get("enriched_tabs") if isinstance(enrichment, dict) else None
        if isinstance(enriched_tabs, dict) and enriched_tabs:
            return (
                {
                    "overview": enriched_tabs.get("overview", {}) or {},
                    "balance_sheet": enriched_tabs.get("balance_sheet", []) or [],
                    "income_statement": enriched_tabs.get("income_statement", []) or [],
                    "cash_flow": enriched_tabs.get("cash_flow", []) or [],
                },
                "enriched_tabs",
            )
        tab_data = transformation.get("tab_data") if isinstance(transformation, dict) else None
        if isinstance(tab_data, dict) and tab_data:
            return (
                {
                    "overview": tab_data.get("overview", {}) or {},
                    "balance_sheet": tab_data.get("balance_sheet", []) or [],
                    "income_statement": tab_data.get("income_statement", []) or [],
                    "cash_flow": tab_data.get("cash_flow", []) or [],
                },
                "tab_data",
            )
        return (
            {
                "overview": transformation.get("overview", {}) if isinstance(transformation, dict) else {},
                "balance_sheet": transformation.get("balance_sheet", []) if isinstance(transformation, dict) else [],
                "income_statement": transformation.get("income_statement", []) if isinstance(transformation, dict) else [],
                "cash_flow": transformation.get("cash_flow", []) if isinstance(transformation, dict) else [],
            },
            "legacy_transformation",
        )

    @staticmethod
    def _load_json_optional(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _preview_type(document_name: str) -> str:
        lowered = document_name.lower()
        if lowered.endswith(".pdf"):
            return "pdf"
        if lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")):
            return "image"
        if lowered.endswith((".txt", ".json", ".csv", ".doc", ".docx", ".xls", ".xlsx")):
            return "download"
        return "unknown"

    @staticmethod
    def _json_status(path: Path) -> str:
        if not path.exists():
            return "failed"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return "failed"
        status = payload.get("status", "failed")
        return status if status in {"success", "partial_success", "failed", "skipped"} else "partial_success"

    @staticmethod
    def _copy_artifact(source_path: Path, target_path: Path) -> None:
        if not source_path.exists():
            raise FileNotFoundError(source_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)

    @staticmethod
    def _derive_overall_status(stages: List[PipelineStageResult], errors: List[str]) -> str:
        if errors or any(stage.status == "failed" for stage in stages):
            if any(stage.status in {"success", "partial_success"} for stage in stages):
                return "partial_success"
            return "failed"
        if any(stage.status == "partial_success" for stage in stages):
            return "partial_success"
        return "success"

    @staticmethod
    def _tail(text: Optional[str], limit: int = 1500) -> Optional[str]:
        if text is None:
            return None
        return text[-limit:]

    @staticmethod
    def _ensure_workspace_dirs(workspace: WorkspacePaths) -> None:
        workspace.current_root.mkdir(parents=True, exist_ok=True)
        workspace.outputs_dir.mkdir(parents=True, exist_ok=True)
        workspace.cam_dir.mkdir(parents=True, exist_ok=True)
        workspace.input_docs_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _count_documents(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(1 for item in path.rglob("*") if item.is_file())

    @staticmethod
    def _generate_application_id(company_name: str) -> str:
        base = "".join(ch.lower() if ch.isalnum() else "-" for ch in company_name).strip("-")
        base = "-".join(part for part in base.split("-") if part)[:40] or "application"
        return f"{base}-{uuid4().hex[:8]}"
