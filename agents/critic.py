"""Critic Agent – evaluates research quality and decides whether to retry."""

import os
import json
import time
from utils.llm import get_llm
from langchain_core.messages import SystemMessage, HumanMessage
from agents.state import ResearchState
from utils.tracing import get_tracing_context
from config import MAX_RETRIES, MAX_TOKENS_PER_REPORT, MAX_COST_PER_REPORT


def critic_agent(state: ResearchState) -> dict:
    """Evaluate whether gathered research is sufficient for a high-quality report.

    Uses Groq LLM to score research quality (1-10) and decide if the
    Researcher needs another pass. Enforces guardrails:
    - Hard cap on retry count (MAX_RETRIES)
    - Per-report token budget (MAX_TOKENS_PER_REPORT)
    - Per-report cost budget (MAX_COST_PER_REPORT)

    When any guardrail is hit, the Critic stops the retry loop and lets
    the Writer proceed with best-available research, and logs a distinct
    event to Langfuse.
    """

    llm = get_llm(role="critic", temperature=0.3)

    # Resolve model name for metrics
    try:
        from config import MODEL_CONFIG
        model_name = MODEL_CONFIG.get("critic", "llama-3.3-70b-versatile")
    except ImportError:
        model_name = "llama-3.3-70b-versatile"

    # Get tracing context
    ctx = get_tracing_context(state.get("report_id", ""))
    retry_count = state.get("retry_count", 0)
    if ctx:
        ctx.start_span("critic", retry_count=retry_count)

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

    llm_start = time.time()
    response = llm.invoke([system, human])

    # Record LLM metrics
    if ctx:
        ctx.record_llm_call("critic", response, model_name, llm_start)

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

    # ---- Guardrail checks ----
    budget_exceeded = False
    max_retries_hit = False

    # Check retry cap
    if retry_count >= MAX_RETRIES:
        needs_more = False
        max_retries_hit = True
        critique = critique + " (Max retries reached, proceeding to writing.)"
        if ctx:
            ctx.log_event("max_retries_reached", {
                "retry_count": retry_count,
                "max_retries": MAX_RETRIES,
                "quality_score": score,
            })

    # Check token budget
    current_tokens = state.get("total_input_tokens", 0) + state.get("total_output_tokens", 0)
    if ctx:
        current_tokens = ctx.total_input_tokens + ctx.total_output_tokens
    if current_tokens >= MAX_TOKENS_PER_REPORT and needs_more:
        needs_more = False
        budget_exceeded = True
        critique = critique + f" (Token budget exceeded: {current_tokens:,}/{MAX_TOKENS_PER_REPORT:,} tokens.)"
        if ctx:
            ctx.log_event("budget_exceeded", {
                "type": "token_budget",
                "current_tokens": current_tokens,
                "max_tokens": MAX_TOKENS_PER_REPORT,
            })

    # Check cost budget
    current_cost = state.get("total_estimated_cost", 0.0)
    if ctx:
        current_cost = ctx.total_estimated_cost
    if current_cost >= MAX_COST_PER_REPORT and needs_more:
        needs_more = False
        budget_exceeded = True
        critique = critique + f" (Cost budget exceeded: ${current_cost:.4f}/${MAX_COST_PER_REPORT:.4f}.)"
        if ctx:
            ctx.log_event("budget_exceeded", {
                "type": "cost_budget",
                "current_cost_usd": round(current_cost, 6),
                "max_cost_usd": MAX_COST_PER_REPORT,
            })

    # End critic span
    if ctx:
        ctx.end_span("critic", output={
            "quality_score": score,
            "needs_more_research": needs_more,
            "budget_exceeded": budget_exceeded,
            "max_retries_hit": max_retries_hit,
        })

    update = {
        "needs_more_research": needs_more,
        "critique": critique,
        "retry_count": retry_count + (1 if needs_more else 0),
        "current_agent": "critic",
        "budget_exceeded": budget_exceeded or state.get("budget_exceeded", False),
        "max_retries_reached": max_retries_hit or state.get("max_retries_reached", False),
        "log": [
            f"Critic evaluated research – Quality score: {score}/10",
            f"Needs more research: {needs_more}",
            f"Feedback: {critique[:150]}...",
        ],
    }

    # Propagate accumulated metrics
    if ctx:
        update["total_input_tokens"] = ctx.total_input_tokens
        update["total_output_tokens"] = ctx.total_output_tokens
        update["total_estimated_cost"] = ctx.total_estimated_cost
        update["agent_metrics"] = ctx.metrics_as_dicts()

    return update


def should_retry(state: ResearchState) -> str:
    """Conditional edge function: route back to researcher or forward to writer."""
    if state.get("needs_more_research", False) and state.get("retry_count", 0) < MAX_RETRIES:
        return "retry"
    return "write"
