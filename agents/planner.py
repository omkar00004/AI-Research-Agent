"""Planner Agent – breaks a research topic into 4-5 focused subtasks."""

import os
import json
import time
from utils.llm import get_llm
from langchain_core.messages import SystemMessage, HumanMessage
from agents.state import ResearchState
from utils.tracing import get_tracing_context


def planner_agent(state: ResearchState) -> dict:
    """Decompose the user topic into focused, researchable subtasks.

    Uses Groq LLM to analyze the topic and produce a JSON list of
    4-5 subtasks that cover different angles of the research topic.
    """

    llm = get_llm(role="planner", temperature=0.4)

    # Resolve model name for metrics
    try:
        from config import MODEL_CONFIG
        model_name = MODEL_CONFIG.get("planner", "llama-3.3-70b-versatile")
    except ImportError:
        model_name = "llama-3.3-70b-versatile"

    # Start tracing span
    ctx = get_tracing_context(state.get("report_id", ""))
    start = time.time()
    if ctx:
        ctx.start_span("planner", retry_count=state.get("retry_count", 0))

    system = SystemMessage(content="""You are a senior research strategist at a top consulting firm.
Given a broad research topic, decompose it into exactly 4-5 focused, non-overlapping subtasks
that together provide comprehensive coverage of the topic.

Each subtask should be:
- Specific enough to search effectively
- Distinct from the others (no overlap)
- Focused on a different angle (e.g. current state, impact, trends, challenges, future outlook)

Respond with ONLY a JSON array of strings, no additional text.
Example: ["subtask 1", "subtask 2", "subtask 3", "subtask 4"]""")

    human = HumanMessage(content=f"Research topic: {state['topic']}")

    response = llm.invoke([system, human])

    # Record LLM call metrics
    metric = None
    if ctx:
        metric = ctx.record_llm_call("planner", response, model_name, start)
        ctx.end_span("planner", output={"subtasks_count": 0})

    try:
        content = response.content.strip()
        # Handle markdown code block wrapping
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        subtasks = json.loads(content.strip())
    except Exception:
        # Fallback: create generic subtasks from the topic
        subtasks = [
            f"Current state and overview of {state['topic']}",
            f"Key challenges and limitations in {state['topic']}",
            f"Recent developments and trends in {state['topic']}",
            f"Future outlook and predictions for {state['topic']}",
        ]

    # Update span output with actual subtask count
    if ctx and ctx._active_spans.get("planner"):
        ctx.end_span("planner", output={"subtasks_count": len(subtasks)})

    # Build metrics update
    result = {
        "subtasks": subtasks,
        "current_agent": "planner",
        "log": [
            f"Analyzed topic: {state['topic']}",
            f"Generated {len(subtasks)} research subtasks",
            *[f"→ {st}" for st in subtasks],
        ],
    }

    # Propagate accumulated metrics
    if ctx:
        result["total_input_tokens"] = ctx.total_input_tokens
        result["total_output_tokens"] = ctx.total_output_tokens
        result["total_estimated_cost"] = ctx.total_estimated_cost
        result["agent_metrics"] = ctx.metrics_as_dicts()

    return result
