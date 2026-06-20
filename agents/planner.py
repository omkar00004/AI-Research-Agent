"""Planner Agent – breaks a research topic into 4-5 focused subtasks."""

import os
import json
from utils.llm import get_llm
from langchain_core.messages import SystemMessage, HumanMessage
from agents.state import ResearchState


def planner_agent(state: ResearchState) -> dict:
    """Decompose the user topic into focused, researchable subtasks.

    Uses Groq LLM to analyze the topic and produce a JSON list of
    4-5 subtasks that cover different angles of the research topic.
    """

    llm = get_llm(temperature=0.4)

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

    return {
        "subtasks": subtasks,
        "current_agent": "planner",
        "log": [
            f"Analyzed topic: {state['topic']}",
            f"Generated {len(subtasks)} research subtasks",
            *[f"→ {st}" for st in subtasks],
        ],
    }
