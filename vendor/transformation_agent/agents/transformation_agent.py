# agents/transformation_agent.py
# ──────────────────────────────────────────────────────────────
# LangGraph pipeline definition for the Transformation Agent.
#
# Wires all 8 nodes into a linear StateGraph:
#
#   document_intake
#        ↓
#   document_identification
#        ↓
#   ocr_extraction
#        ↓
#   table_detection
#        ↓
#   data_cleaning (agent)
#        ↓
#   data_structuring (agent)
#        ↓
#   tab_mapping
#        ↓
#   output_generation
#        ↓
#       END
#
# LangChain handles all LLM calls inside each node.
# LangGraph handles the state passing and execution order.
# AgentState is the shared memory that flows through every node.
# ──────────────────────────────────────────────────────────────

from langgraph.graph import StateGraph, END
from state.agent_state import AgentState

from nodes.document_intake         import document_intake_node
from nodes.document_identification import document_identification_node
from nodes.ocr_extraction          import ocr_extraction_node
from nodes.table_detection         import table_detection_node
from nodes.data_cleaning           import data_cleaning_node
from nodes.data_structuring        import data_structuring_node
from nodes.tab_mapping             import tab_mapping_node
from nodes.output_generation       import output_generation_node


def build_transformation_agent():
    """
    Build and compile the Transformation Agent LangGraph pipeline.

    Returns:
        A compiled LangGraph StateGraph ready to invoke with initial_state.

    Usage:
        agent = build_transformation_agent()
        result = agent.invoke(initial_state)
    """

    # ── Create graph with shared state ───────────────────────
    graph = StateGraph(AgentState)

    # ── Register all 8 nodes ─────────────────────────────────
    graph.add_node("document_intake",         document_intake_node)
    graph.add_node("document_identification", document_identification_node)
    graph.add_node("ocr_extraction",          ocr_extraction_node)
    graph.add_node("table_detection",         table_detection_node)
    graph.add_node("data_cleaning",           data_cleaning_node)
    graph.add_node("data_structuring",        data_structuring_node)
    graph.add_node("tab_mapping",             tab_mapping_node)
    graph.add_node("output_generation",       output_generation_node)

    # ── Define linear execution order ────────────────────────
    graph.set_entry_point("document_intake")
    graph.add_edge("document_intake",         "document_identification")
    graph.add_edge("document_identification", "ocr_extraction")
    graph.add_edge("ocr_extraction",          "table_detection")
    graph.add_edge("table_detection",         "data_cleaning")
    graph.add_edge("data_cleaning",           "data_structuring")
    graph.add_edge("data_structuring",        "tab_mapping")
    graph.add_edge("tab_mapping",             "output_generation")
    graph.add_edge("output_generation",       END)

    # ── Compile and return ────────────────────────────────────
    # Compilation validates:
    #   - All node names referenced in edges exist
    #   - Entry point is defined
    #   - Graph is acyclic
    return graph.compile()
