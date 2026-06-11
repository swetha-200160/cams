"""Tests for run_pipeline and generate_cam in orchestrator_service.py.

Agents are never spawned — _run_agent1/2/3 and _generate_cam_draft are
replaced with lambdas that write minimal JSON artifacts and return the
expected PipelineStageResult objects.

Real sample borrower documents from:
  C:\\Users\\abdula\\Downloads\\transformation_agent 1\\transformation_agent\\input_docs
are used for tests that verify document-copying behaviour.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    ApplicationCreateRequest,
    PipelineRunRequest,
    PipelineStageResult,
)
from orchestrator_service import UnifiedOrchestratorService, WorkspacePaths


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SAMPLE_DOCS_DIR = Path(
    r"C:\Users\abdula\Downloads\transformation_agent 1\transformation_agent\input_docs"
)
sample_docs_available = pytest.mark.skipif(
    not SAMPLE_DOCS_DIR.exists(), reason="Sample input docs not available on this machine"
)

MINIMAL_TRANSFORMATION = {
    "status": "success",
    "tab_data": {
        "overview": {"company_name": "Test Corp", "industry": "IT"},
        "balance_sheet": [{"year": "FY2024", "total_assets": 1000, "equity": 400}],
        "income_statement": [{"year": "FY2024", "revenue": 5000, "pat": 400}],
        "cash_flow": [{"year": "FY2024", "operating_cash_flow": 600}],
    },
    "summary": {"documents_processed": [], "error_count": 0, "run_timestamp": "2024-01-01T00:00:00Z"},
    "auxiliary_data": {},
    "missing_fields": {},
    "errors": [],
}

MINIMAL_ENRICHMENT = {
    "status": "success",
    "enriched_tabs": {
        "overview": {"company_name": "Test Corp"},
        "balance_sheet": [],
        "income_statement": [],
        "cash_flow": [],
    },
    "summary": {"run_timestamp": "2024-01-01T00:00:00Z", "fields_scraped": 0, "fields_flagged": 0, "sources_used": [], "errors": []},
    "retrieved_fields": [],
    "flagged_manual": [],
    "raw_scraped_data": {},
}

MINIMAL_ANALYSIS = {
    "status": "success",
    "company_name": "Test Corp",
    "ratio_report": {"current_ratio": 1.5, "dscr": 1.2},
    "trend_report": {},
    "banking_behaviour": {},
    "cash_flow_projection": {},
    "gst_analytics": {},
    "tax_compliance": {},
    "related_party": {},
    "industry_intelligence": {},
    "market_risk": {},
    "parsed_financials": {},
    "agents_executed": ["ratio_analysis_agent"],
    "agents_failed": [],
    "errors": [],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_service(tmp_path: Path) -> UnifiedOrchestratorService:
    svc = UnifiedOrchestratorService.__new__(UnifiedOrchestratorService)
    svc.workspace_root = tmp_path / "workspaces"
    svc.workspace_root.mkdir(parents=True, exist_ok=True)
    svc.vendor_root = tmp_path / "vendor"
    svc.vendor_root.mkdir(parents=True, exist_ok=True)
    return svc


def _create_app(svc, company="Test Corp", app_id="test-app-001"):
    return svc.create_application(
        ApplicationCreateRequest(company_name=company, application_id=app_id)
    ).application_id


def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Stage mock factories — write artifact to disk, return PipelineStageResult
# ---------------------------------------------------------------------------
def _agent1_success(ws: WorkspacePaths) -> PipelineStageResult:
    out = _write_json(ws.outputs_dir / "transformation_output.json", MINIMAL_TRANSFORMATION)
    return PipelineStageResult(stage="transformation", status="success", output_path=str(out))


def _agent1_failed(ws: WorkspacePaths) -> PipelineStageResult:
    # Write a failed JSON so _write_agent2_input_adapter wouldn't even be called
    return PipelineStageResult(stage="transformation", status="failed")


def _agent2_input_adapter(path: Path, output_dir: Path) -> Path:
    mapped = {"status": "success", "tab_data": {"overview": {}, "balance_sheet": [], "income_statement": [], "cash_flow": []}, "auxiliary_data": {}, "missing_fields": {}, "errors": [], "summary": {}}
    out = _write_json(output_dir / "transformation_output_for_agent2.json", mapped)
    return out


def _agent2_success(ws: WorkspacePaths, _input: Path) -> PipelineStageResult:
    out = _write_json(ws.outputs_dir / "enrich_output.json", MINIMAL_ENRICHMENT)
    return PipelineStageResult(stage="enrichment", status="success", output_path=str(out))


def _agent2_failed(ws: WorkspacePaths, _input: Path) -> PipelineStageResult:
    return PipelineStageResult(stage="enrichment", status="failed")


def _agent3_success(ws: WorkspacePaths, _enrich: Path) -> PipelineStageResult:
    out = _write_json(ws.outputs_dir / "analysis_output.json", MINIMAL_ANALYSIS)
    return PipelineStageResult(stage="analysis", status="success", output_path=str(out))


def _agent3_failed(ws: WorkspacePaths, _enrich: Path) -> PipelineStageResult:
    return PipelineStageResult(stage="analysis", status="failed")


def _cam_gen_success(workspace: WorkspacePaths, company_name: str, transformation_path: Path, enrichment_path: Path, analysis_path: Path) -> PipelineStageResult:
    draft_path = workspace.cam_dir / "cam_draft.json"
    workspace.cam_dir.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(json.dumps({
        "application_id": workspace.application_root.name,
        "company_name": company_name or "Test Corp",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": [],
        "source_documents": [],
        "notes": [],
    }), encoding="utf-8")
    return PipelineStageResult(
        stage="cam_generation",
        status="success",
        output_path=str(draft_path),
        details={"sections": 0, "markdown_path": str(workspace.cam_dir / "cam_draft.md"), "docx_path": str(workspace.cam_dir / "cam_draft.docx")},
    )


def _patch_all_success(svc: UnifiedOrchestratorService):
    """Monkey-patch all 4 stage methods with successful stubs."""
    svc._run_agent1 = _agent1_success
    svc._write_agent2_input_adapter = _agent2_input_adapter
    svc._run_agent2 = _agent2_success
    svc._run_agent3 = _agent3_success
    svc._generate_cam_draft = _cam_gen_success


# ---------------------------------------------------------------------------
# run_pipeline — happy path
# ---------------------------------------------------------------------------
class TestRunPipelineSuccess:
    def test_returns_success_status(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id, company_name="Test Corp"))
        assert resp.status == "success"

    def test_all_four_stages_present(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id))
        stage_names = [s.stage for s in resp.stages]
        assert "transformation" in stage_names
        assert "enrichment" in stage_names
        assert "analysis" in stage_names
        assert "cam_generation" in stage_names

    def test_artifacts_populated(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id))
        assert resp.artifacts.transformation_output is not None
        assert resp.artifacts.enrichment_output is not None
        assert resp.artifacts.analysis_output is not None
        assert resp.artifacts.cam_draft_output is not None

    def test_timestamps_set(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id))
        assert resp.started_at is not None
        assert resp.completed_at is not None
        assert resp.completed_at >= resp.started_at

    def test_no_errors_on_success(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id))
        assert resp.errors == []

    def test_status_persisted_to_disk(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        svc.run_pipeline(PipelineRunRequest(application_id=app_id))
        status = svc.get_status(app_id)
        # Full pipeline with draft generation → application lifecycle status is draft_ready
        assert status.status == "draft_ready"
        assert status.draft_available is True

    def test_application_record_updated_to_draft_ready(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        svc.run_pipeline(PipelineRunRequest(application_id=app_id))
        record = svc.get_application(app_id)
        assert record.status == "draft_ready"

    def test_company_name_propagated(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc, company="Original Corp")
        _patch_all_success(svc)
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id, company_name="Override Corp"))
        assert resp.company_name == "Override Corp"


# ---------------------------------------------------------------------------
# run_pipeline — generate_draft=False
# ---------------------------------------------------------------------------
class TestRunPipelineNoDraft:
    def test_skips_cam_generation_stage(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id, generate_draft=False))
        stage_names = [s.stage for s in resp.stages]
        assert "cam_generation" not in stage_names
        assert len(stage_names) == 3

    def test_still_returns_success(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id, generate_draft=False))
        assert resp.status == "success"

    def test_cam_draft_artifact_is_none(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id, generate_draft=False))
        assert resp.artifacts.cam_draft_output is None


# ---------------------------------------------------------------------------
# run_pipeline — stage failures stop the pipeline
# ---------------------------------------------------------------------------
class TestRunPipelineFailure:
    def test_agent1_failure_stops_pipeline(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        svc._run_agent1 = _agent1_failed
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id))
        assert resp.status == "failed"
        stage_names = [s.stage for s in resp.stages]
        assert "enrichment" not in stage_names
        assert "analysis" not in stage_names

    def test_agent1_failure_records_error(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        svc._run_agent1 = _agent1_failed
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id))
        assert len(resp.errors) > 0

    def test_agent2_failure_stops_pipeline(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        svc._run_agent1 = _agent1_success
        svc._write_agent2_input_adapter = _agent2_input_adapter
        svc._run_agent2 = _agent2_failed
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id))
        # transformation succeeded, enrichment failed → partial_success or failed
        assert resp.status in ("failed", "partial_success")
        stage_names = [s.stage for s in resp.stages]
        assert "analysis" not in stage_names

    def test_agent3_failure_stops_cam_generation(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        svc._run_agent1 = _agent1_success
        svc._write_agent2_input_adapter = _agent2_input_adapter
        svc._run_agent2 = _agent2_success
        svc._run_agent3 = _agent3_failed
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id))
        # two stages succeeded, analysis failed → partial_success or failed
        assert resp.status in ("failed", "partial_success")
        stage_names = [s.stage for s in resp.stages]
        assert "cam_generation" not in stage_names

    def test_failed_status_persisted_to_disk(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        svc._run_agent1 = _agent1_failed
        svc.run_pipeline(PipelineRunRequest(application_id=app_id))
        status = svc.get_status(app_id)
        assert status.status == "failed"

    def test_partial_success_when_some_stages_pass(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        svc._run_agent1 = _agent1_success
        svc._write_agent2_input_adapter = _agent2_input_adapter
        svc._run_agent2 = _agent2_failed
        resp = svc.run_pipeline(PipelineRunRequest(application_id=app_id))
        # One stage passed (transformation), one failed — partial_success or failed
        assert resp.status in ("failed", "partial_success")


# ---------------------------------------------------------------------------
# run_pipeline — with real sample documents as input_documents_dir
# ---------------------------------------------------------------------------
class TestRunPipelineWithSampleDocs:
    @sample_docs_available
    def test_documents_copied_into_workspace(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        svc.run_pipeline(PipelineRunRequest(
            application_id=app_id,
            input_documents_dir=str(SAMPLE_DOCS_DIR),
        ))
        input_docs = svc.workspace_root / app_id / "current" / "input_docs"
        copied = list(input_docs.iterdir())
        assert len(copied) > 0

    @sample_docs_available
    def test_expected_doc_types_present(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        svc.run_pipeline(PipelineRunRequest(
            application_id=app_id,
            input_documents_dir=str(SAMPLE_DOCS_DIR),
        ))
        input_docs = svc.workspace_root / app_id / "current" / "input_docs"
        extensions = {f.suffix.lower() for f in input_docs.rglob("*") if f.is_file()}
        # Sample set has xlsx, docx, csv, pdf, png
        assert ".xlsx" in extensions
        assert ".docx" in extensions
        assert ".pdf" in extensions

    @sample_docs_available
    def test_document_count_matches_sample(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        _patch_all_success(svc)
        svc.run_pipeline(PipelineRunRequest(
            application_id=app_id,
            input_documents_dir=str(SAMPLE_DOCS_DIR),
        ))
        expected = sum(1 for f in SAMPLE_DOCS_DIR.rglob("*") if f.is_file())
        input_docs = svc.workspace_root / app_id / "current" / "input_docs"
        actual = sum(1 for f in input_docs.rglob("*") if f.is_file())
        assert actual == expected

    @sample_docs_available
    def test_invalid_input_dir_raises_before_pipeline(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        with pytest.raises(FileNotFoundError):
            svc.run_pipeline(PipelineRunRequest(
                application_id=app_id,
                input_documents_dir="/this/path/does/not/exist",
            ))

    @sample_docs_available
    def test_pipeline_succeeds_end_to_end_with_sample_docs(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc, company="HDFC Test Corp")
        _patch_all_success(svc)
        resp = svc.run_pipeline(PipelineRunRequest(
            application_id=app_id,
            company_name="HDFC Test Corp",
            input_documents_dir=str(SAMPLE_DOCS_DIR),
        ))
        assert resp.status == "success"
        assert resp.artifacts.cam_draft_output is not None


# ---------------------------------------------------------------------------
# generate_cam — standalone re-generation
# ---------------------------------------------------------------------------
class TestGenerateCam:
    def _write_all_outputs(self, svc: UnifiedOrchestratorService, app_id: str):
        outputs = svc.workspace_root / app_id / "current" / "outputs"
        outputs.mkdir(parents=True, exist_ok=True)
        _write_json(outputs / "transformation_output.json", MINIMAL_TRANSFORMATION)
        _write_json(outputs / "enrich_output.json", MINIMAL_ENRICHMENT)
        _write_json(outputs / "analysis_output.json", MINIMAL_ANALYSIS)

    def test_generates_cam_draft(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        self._write_all_outputs(svc, app_id)
        svc._generate_cam_draft = _cam_gen_success
        result = svc.generate_cam(app_id)
        assert result["status"] == "success"
        assert result["application_id"] == app_id

    def test_missing_transformation_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        # Only write enrichment + analysis, not transformation
        outputs = svc.workspace_root / app_id / "current" / "outputs"
        outputs.mkdir(parents=True, exist_ok=True)
        _write_json(outputs / "enrich_output.json", MINIMAL_ENRICHMENT)
        _write_json(outputs / "analysis_output.json", MINIMAL_ANALYSIS)
        with pytest.raises(FileNotFoundError):
            svc.generate_cam(app_id)

    def test_missing_enrichment_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        outputs = svc.workspace_root / app_id / "current" / "outputs"
        outputs.mkdir(parents=True, exist_ok=True)
        _write_json(outputs / "transformation_output.json", MINIMAL_TRANSFORMATION)
        _write_json(outputs / "analysis_output.json", MINIMAL_ANALYSIS)
        with pytest.raises(FileNotFoundError):
            svc.generate_cam(app_id)

    def test_missing_analysis_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        outputs = svc.workspace_root / app_id / "current" / "outputs"
        outputs.mkdir(parents=True, exist_ok=True)
        _write_json(outputs / "transformation_output.json", MINIMAL_TRANSFORMATION)
        _write_json(outputs / "enrich_output.json", MINIMAL_ENRICHMENT)
        with pytest.raises(FileNotFoundError):
            svc.generate_cam(app_id)

    def test_status_updated_to_draft_ready(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        self._write_all_outputs(svc, app_id)
        svc._generate_cam_draft = _cam_gen_success
        svc.generate_cam(app_id)
        record = svc.get_application(app_id)
        assert record.status == "draft_ready"

    def test_artifacts_returned(self, tmp_path):
        svc = _make_service(tmp_path)
        app_id = _create_app(svc)
        self._write_all_outputs(svc, app_id)
        svc._generate_cam_draft = _cam_gen_success
        result = svc.generate_cam(app_id)
        assert "artifacts" in result

    def test_company_name_override(self, tmp_path):
        from models import GenerateCamRequest
        svc = _make_service(tmp_path)
        app_id = _create_app(svc, company="Original Corp")
        self._write_all_outputs(svc, app_id)
        svc._generate_cam_draft = _cam_gen_success
        result = svc.generate_cam(app_id, GenerateCamRequest(company_name="Override Corp"))
        assert result["company_name"] == "Override Corp"
