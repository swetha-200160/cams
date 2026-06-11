# Integrated CAM Backend Final

This package is the backend-only integrated build with:
- minimal FastAPI orchestrator
- latest Agent 1 cleaned patch source included under `agent1_patch_source/`
- Agent 2 and Agent 3 vendored as isolated runtime dependencies

Important note:
The uploaded Agent 1 cleanup files were a partial source patch set, not a complete standalone repo.
So they are included here as `agent1_patch_source/` and the orchestrator compatibility layer was updated to accept the cleaned Agent 1 output contract (`tab_data`, `auxiliary_data`, `missing_fields`, `input_completeness`) in addition to the earlier contract.

# CAM Backend Minimal

Flattened backend-only orchestrator.

## Files
- main.py
- settings.py
- models.py
- orchestrator_service.py
- generation_service.py
- source_locator.py
- export_service.py
- agent2_mapper.py
- agent3_mapper.py
- run_agent3.py
- vendor/
- workspaces/

## Run
```bash
pip install -r requirements.txt
pip install -r vendor/transformation_agent/requirements.txt
pip install -r vendor/web_scraper_agent_v2/requirements.txt
pip install -r vendor/agent3_analysis/agent3_analysis/requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8010
```
