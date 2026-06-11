from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from mappers import map_enrich_output_to_agent3_payload


def run_agent3(
    enrich_output_dict: dict,
    vendor_root: Path,
    groq_api_key: str,
    pre_mapped: bool = False,
) -> dict:
    """
    Run Agent 3 analysis in-process.

    Parameters
    ----------
    enrich_output_dict
        Either Agent 2's raw enrich_output (pre_mapped=False, default) or an
        already-mapped Agent2Output-compatible dict (pre_mapped=True, used by
        the DB bypass flow via map_db_json_to_agent3_payload).
    vendor_root
        Path to the vendor/agent3_analysis directory.
    groq_api_key
        Groq API key for LLM-powered sub-agents.
    pre_mapped
        When True, skip map_enrich_output_to_agent3_payload and validate the
        dict directly against Agent2Output.
    """
    vendor_root = Path(vendor_root).resolve()
    vendor_root_str = str(vendor_root)
    if vendor_root_str not in sys.path:
        sys.path.insert(0, vendor_root_str)

    from agent3_analysis.orchestrator import run_analysis  # type: ignore
    from agent3_analysis.schemas.input_schema import Agent2Output  # type: ignore

    if pre_mapped:
        mapped_payload = enrich_output_dict
    else:
        mapped_payload = map_enrich_output_to_agent3_payload(enrich_output_dict)
    validated_payload = Agent2Output.model_validate(mapped_payload)
    result = run_analysis(validated_payload, groq_api_key)
    return result.model_dump(mode="json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Agent 3 against Agent 2 enrich output")
    parser.add_argument("--input", required=True, help="Path to Agent 2 enrich_output.json")
    parser.add_argument("--output", required=True, help="Where to write Agent 3 insights_output.json")
    parser.add_argument("--vendor-root", required=True, help="Path to vendor/agent3_analysis")
    args = parser.parse_args()

    vendor_root = Path(args.vendor_root).resolve()
    sys.path.insert(0, str(vendor_root))

    from agent3_analysis.orchestrator import run_analysis  # type: ignore
    from agent3_analysis.schemas.input_schema import Agent2Output  # type: ignore

    input_payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    mapped_payload = map_enrich_output_to_agent3_payload(input_payload)
    validated_payload = Agent2Output.model_validate(mapped_payload)

    import os
    groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not groq_api_key:
        raise RuntimeError("GROQ_API_KEY is required for Agent 3 execution.")

    result = run_analysis(validated_payload, groq_api_key)
    Path(args.output).write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
