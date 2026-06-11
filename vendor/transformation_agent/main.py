# main.py
# ──────────────────────────────────────────────────────────────
# CAMS Transformation Agent — Entry Point
#
# Run:
#   python main.py
#
# What it does:
#   1. Validates GROQ_API_KEY is present in .env
#   2. Clears the Docling parse cache (fresh run)
#   3. Builds the LangGraph pipeline
#   4. Initializes the AgentState with all required empty fields
#   5. Invokes the agent — runs all 8 nodes sequentially
#   6. Prints a completion summary
#
# Output:
#   output/transformation_output.json
#   Ready for consumption by Agent 2 (Web Scraper) and
#   Agent 3 (Analysis Agent).
# ──────────────────────────────────────────────────────────────

import os
import sys
import warnings
from dotenv import load_dotenv

# Suppress Pydantic UserWarnings about protected namespaces
warnings.filterwarnings("ignore", category=UserWarning, message="Field .* has conflict with protected namespace")

from agents.transformation_agent import build_transformation_agent
from config.settings import INPUT_FOLDER
from tools.docling_reader import clear_cache

# Load .env before anything else
load_dotenv()


def validate_environment():
    """
    Check all required environment variables and system dependencies
    before starting the pipeline. Exit with a clear message if anything
    is missing.
    """
    errors = []

    # ── Groq API key ──────────────────────────────────────────
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key or api_key == "your_groq_api_key_here":
        errors.append(
            "GROQ_API_KEY is not set or still has the placeholder value.\n"
            "  → Get a free key at https://console.groq.com\n"
            "  → Add it to your .env file: GROQ_API_KEY=gsk_..."
        )

    # ── Input folder ──────────────────────────────────────────
    if not os.path.exists(INPUT_FOLDER):
        errors.append(
            f"Input folder '{INPUT_FOLDER}' does not exist.\n"
            f"  → Create it and drop your financial documents inside."
        )

    # ── Output folder ─────────────────────────────────────────
    os.makedirs("output", exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    if errors:
        print("\n❌ Environment validation failed:\n")
        for err in errors:
            print(f"  • {err}\n")
        sys.exit(1)

    print("✅ Environment validated")


def build_initial_state() -> dict:
    """
    Build the initial AgentState dict.
    Every field defined in AgentState TypedDict must be present here
    or LangGraph will raise a KeyError on first node access.
    """
    return {
        # Input
        "input_folder":        INPUT_FOLDER,

        # Node outputs — all empty at start
        "document_repository":  [],
        "classified_documents": [],
        "extracted_texts":      {},
        "extracted_tables":     {},
        "extracted_sections":   {},
        "cleaned_data":         {},
        "structured_datasets":  {},
        "tab_data":             {},
        "final_output":         {},

        # Metadata
        "errors":               [],
        "current_step":         "start",
    }


if __name__ == "__main__":
    print("\n" + "═" * 55)
    print("   🚀  CAMS — Transformation Agent")
    print("   ⚡  Groq API  |  Llama 3.1 8B  |  Docling")
    print("═" * 55 + "\n")

    # ── Step 1: Validate environment ──────────────────────────
    validate_environment()

    # ── Step 2: Clear Docling parse cache ─────────────────────
    # Ensures each pipeline run starts with a fresh cache.
    # Prevents stale data if input_docs/ contents changed.
    clear_cache()
    print("✅ Parse cache cleared\n")

    # ── Step 3: Build LangGraph pipeline ─────────────────────
    print("🔧 Building LangGraph pipeline...")
    try:
        agent = build_transformation_agent()
        print("✅ Pipeline compiled successfully\n")
    except Exception as e:
        print(f"❌ Failed to build pipeline: {e}")
        sys.exit(1)

    # ── Step 4: Run pipeline ──────────────────────────────────
    initial_state = build_initial_state()

    print(f"📂 Input folder  : {INPUT_FOLDER}")
    doc_count = len([
        f for f in os.listdir(INPUT_FOLDER)
        if not f.startswith(".")
    ]) if os.path.exists(INPUT_FOLDER) else 0
    print(f"📄 Documents found: {doc_count}\n")

    try:
        result = agent.invoke(initial_state)
    except Exception as e:
        print(f"\n❌ Pipeline crashed at top level: {e}")
        print("   Check the error log above for which node failed.")
        sys.exit(1)

    # ── Step 5: Final summary ─────────────────────────────────
    final = result.get("final_output", {})
    status = final.get("status", "unknown")
    error_count = len(result.get("errors", []))

    print("\n" + "═" * 55)
    print("   ✅  Transformation Agent Complete")
    print("═" * 55)
    print(f"   Status        : {status.upper()}")
    print(f"   Errors        : {error_count}")
    print(f"   Output file   : output/transformation_output.json")
    print("═" * 55 + "\n")

    if error_count > 0:
        print("⚠️  Errors encountered (non-fatal):")
        for err in result.get("errors", []):
            print(f"   • {err}")
        print()
