"""Research Agent – uses Tavily search + Groq LLM synthesis per subtask."""

import os
from utils.llm import get_llm
from langchain_core.messages import SystemMessage, HumanMessage
from tavily import TavilyClient
from agents.state import ResearchState


def research_agent(state: ResearchState) -> dict:
    """Search the web for each subtask and synthesize findings.

    For every subtask from the Planner, runs a Tavily search to find
    authoritative sources, then uses Groq LLM to synthesize the raw
    search results into a coherent research summary.
    """

    llm = get_llm(temperature=0.3)

    tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

    research_results = []
    all_sources = list(state.get("sources", []))
    log_entries = []

    for idx, subtask in enumerate(state["subtasks"], 1):
        log_entries.append(f"Researching ({idx}/{len(state['subtasks'])}): {subtask[:80]}")

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

        response = llm.invoke([system, human])

        research_results.append({
            "subtask": subtask,
            "synthesis": response.content,
            "sources": subtask_sources,
        })

        log_entries.append(f"✓ Found {len(results)} sources, synthesized findings")

    return {
        "research_results": research_results,
        "sources": all_sources,
        "current_agent": "researcher",
        "log": log_entries,
    }
