"""Research Agent – uses Tavily search + Groq LLM synthesis per subtask."""

import os
import time
from utils.llm import get_llm
from langchain_core.messages import SystemMessage, HumanMessage
from tavily import TavilyClient
from agents.state import ResearchState
from utils.tracing import get_tracing_context


def research_agent(state: ResearchState) -> dict:
    """Search the web for each subtask and synthesize findings.

    For every subtask from the Planner, runs a Tavily search to find
    authoritative sources, then uses Groq LLM to synthesize the raw
    search results into a coherent research summary.
    """

    llm = get_llm(role="researcher", temperature=0.3)

    # Resolve model name for metrics
    try:
        from config import MODEL_CONFIG
        model_name = MODEL_CONFIG.get("researcher", "llama-3.3-70b-versatile")
    except ImportError:
        model_name = "llama-3.3-70b-versatile"

    tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

    research_results = []
    all_sources = list(state.get("sources", []))
    log_entries = []

    # Get tracing context
    ctx = get_tracing_context(state.get("report_id", ""))
    retry_count = state.get("retry_count", 0)
    if ctx:
        ctx.start_span("researcher", retry_count=retry_count)

    for idx, subtask in enumerate(state["subtasks"], 1):
        log_entries.append(f"Researching ({idx}/{len(state['subtasks'])}): {subtask[:80]}")

        # Start child span for this subtask
        child_name = f"researcher_subtask_{idx}"
        subtask_start = time.time()
        if ctx:
            ctx.start_child_span("researcher", child_name, subtask_index=idx)

        # Search with Tavily
        try:
            search_response = tavily.search(
                query=subtask,
                search_depth="advanced",
                max_results=5,
                include_answer=True,
            )
            results = search_response.get("results", [])
        except Exception as e:
            log_entries.append(f"⚠ Tavily search failed for subtask {idx}: {str(e)[:100]}")
            results = []

        # Collect sources
        subtask_sources = []
        for r in results:
            source = {"title": r.get("title", ""), "url": r.get("url", "")}
            subtask_sources.append(source)
            all_sources.append(source)

        # Build context from search results
        context = "\n\n".join([
            f"Source: {r.get('title', 'N/A')}\nURL: {r.get('url', '')}\nContent: {r.get('content', '')[:500]}"
            for r in results
        ]) if results else "No search results found."

        # Synthesize with LLM
        system = SystemMessage(content="""You are a senior research analyst. Given search results about a specific subtask,
synthesize the information into a clear, well-structured research summary.

Guidelines:
- Focus on facts, data points, and expert opinions
- Cite specific findings from the sources
- Note any conflicting viewpoints
- Keep the synthesis concise but thorough (200-400 words)
- Write in a professional, analytical tone""")

        human = HumanMessage(content=f"""Subtask: {subtask}

Search Results:
{context}

Synthesize these findings into a research summary:""")

        llm_start = time.time()
        response = llm.invoke([system, human])

        # Record LLM metrics for this subtask
        if ctx:
            ctx.record_llm_call("researcher", response, model_name, llm_start, span_name=child_name)
            ctx.end_child_span(child_name, output={"sources_found": len(results)})

        research_results.append({
            "subtask": subtask,
            "synthesis": response.content,
            "sources": subtask_sources,
        })

        log_entries.append(f"✓ Found {len(results)} sources, synthesized findings")

    # End researcher span
    if ctx:
        ctx.end_span("researcher", output={"subtasks_researched": len(research_results)})

    result = {
        "research_results": research_results,
        "sources": all_sources,
        "current_agent": "researcher",
        "log": log_entries,
    }

    # Propagate accumulated metrics
    if ctx:
        result["total_input_tokens"] = ctx.total_input_tokens
        result["total_output_tokens"] = ctx.total_output_tokens
        result["total_estimated_cost"] = ctx.total_estimated_cost
        result["agent_metrics"] = ctx.metrics_as_dicts()

    return result
