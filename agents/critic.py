"""Critic Agent – evaluates research quality and decides whether to retry."""

import os
import json
from utils.llm import get_llm
from langchain_core.messages import SystemMessage, HumanMessage
from agents.state import ResearchState


def critic_agent(state: ResearchState) -> dict:
    """Evaluate whether gathered research is sufficient for a high-quality report.

    Uses Groq LLM to score research quality (1-10) and decide if the
    Researcher needs another pass. Caps retries at 2 to prevent loops.
    """

    llm = get_llm(temperature=0.3)

    research_summary = "\n\n".join([
        f"Subtask: {r['subtask']}\nFindings: {r['synthesis'][:400]}..."
        for r in state["research_results"]
    ])

    system = SystemMessage(content="""You are a critical research editor at a top consulting firm.
Evaluate whether the research gathered is sufficient to write a comprehensive, accurate report.

Respond with ONLY a JSON object with these fields:
{
  "needs_more_research": true/false,
  "critique": "specific feedback on what is missing or insufficient",
  "quality_score": 1-10
}

Be strict. If key aspects of the topic are missing or sources are thin, request more research.
If research is solid (score >= 7), set needs_more_research to false.""")

    human = HumanMessage(content=f"""Topic: {state['topic']}

Research gathered:
{research_summary}

Total sources: {len(state['sources'])}

Evaluate this research:""")

    response = llm.invoke([system, human])

    try:
        content = response.content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        result = json.loads(content.strip())
        needs_more = result.get("needs_more_research", False)
        critique = result.get("critique", "Research is sufficient.")
        score = result.get("quality_score", 7)
    except Exception:
        needs_more = False
        critique = "Research evaluated as sufficient."
        score = 7

    # Cap retries at 2
    if state.get("retry_count", 0) >= 2:
        needs_more = False
        critique = critique + " (Max retries reached, proceeding to writing.)"

    return {
        "needs_more_research": needs_more,
        "critique": critique,
        "retry_count": state.get("retry_count", 0) + (1 if needs_more else 0),
        "current_agent": "critic",
        "log": [
            f"Critic evaluated research – Quality score: {score}/10",
            f"Needs more research: {needs_more}",
            f"Feedback: {critique[:150]}...",
        ],
    }


def should_retry(state: ResearchState) -> str:
    """Conditional edge function: route back to researcher or forward to writer."""
    if state.get("needs_more_research", False) and state.get("retry_count", 0) < 2:
        return "retry"
    return "write"
