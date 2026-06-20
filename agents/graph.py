"""LangGraph agent graph with conditional Critic retry loop."""

from langgraph.graph import StateGraph, END
from agents.state import ResearchState
from agents.planner import planner_agent
from agents.researcher import research_agent
from agents.critic import critic_agent, should_retry
from agents.writer import writer_agent


def build_graph():
    """Build and compile the multi-agent research pipeline.

    Graph topology:
        planner → researcher → critic ─┬─(retry)─→ researcher
                                        └─(write)─→ writer → END
    """

    graph = StateGraph(ResearchState)

    # Add nodes
    graph.add_node("planner", planner_agent)
    graph.add_node("researcher", research_agent)
    graph.add_node("critic", critic_agent)
    graph.add_node("writer", writer_agent)

    # Linear flow: planner -> researcher -> critic
    graph.set_entry_point("planner")
    graph.add_edge("planner", "researcher")
    graph.add_edge("researcher", "critic")

    # Conditional: critic decides retry or write
    graph.add_conditional_edges(
        "critic",
        should_retry,
        {
            "retry": "researcher",
            "write": "writer",
        }
    )

    graph.add_edge("writer", END)

    return graph.compile()
