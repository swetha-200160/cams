from __future__ import annotations

import os
from pathlib import Path
from pydantic import BaseModel, Field

# Load .env from the project root so all API keys are available to the server process
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=_env_file, override=False)
    except ImportError:
        import os
        for _line in _env_file.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if _k.strip() not in os.environ:
                    os.environ[_k.strip()] = _v.strip()


class AppSettings(BaseModel):
    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parent)
    workspace_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parent / "workspaces")
    vendor_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parent / "vendor")
    host: str = Field(default_factory=lambda: os.environ.get("HOST", "0.0.0.0"))
    port: int = Field(default_factory=lambda: int(os.environ.get("PORT", "8010")))
    title: str = "CAMS Unified Orchestrator"
    version: str = "1.0.0"


settings = AppSettings()
settings.workspace_root.mkdir(parents=True, exist_ok=True)
