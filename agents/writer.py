"""Writer Agent – synthesizes all research into a structured markdown report."""

import os
from utils.llm import get_llm
from langchain_core.messages import SystemMessage, HumanMessage
from agents.state import ResearchState


def writer_agent(state: ResearchState) -> dict:
    """Compose the final structured research report from all gathered findings.

    Takes the synthesized research results from all subtasks and produces
    a professional markdown report with executive summary, findings per
    subtask, key insights, and conclusion.
    """

    llm = get_llm(temperature=0.4)

    # Build research context
    research_context = "\n\n---\n\n".join([
        f"## Subtask: {r['subtask']}\n\n{r['synthesis']}"
        for r in state["research_results"]
    ])

    # Include critic feedback if available
    critic_note = ""
    if state.get("critique"):
        critic_note = f"\n\nCritic feedback (incorporate into the report): {state['critique']}"

    system = SystemMessage(content="""You are a world-class research report writer at a top consulting firm.
Write a comprehensive, professional research report based on the provided research findings.

Structure the report with these sections:
## Executive Summary
3-4 sentences summarizing the key findings.

## Introduction
Context, relevance, and scope of the research topic.

## Findings
One subsection (### heading) per research subtask. Synthesize the content into analytical paragraphs.
Include specific data points, statistics, and expert viewpoints.

## Key Insights
Top 3-5 actionable takeaways as bullet points.

## Conclusion
Summary of overall findings and forward-looking statement.

Guidelines:
- Write in a professional, analytical tone
- Use rich markdown formatting: headings (##, ###), **bold**, *italic*, bullet lists, numbered lists
- Use markdown tables when comparing data, metrics, or features side-by-side
- Use blockquotes (>) for notable expert quotes or key statistics
- When the topic involves processes, workflows, architectures, relationships, comparisons, or systems, include a Mermaid diagram using a ```mermaid code block to visually illustrate the concept. Use graph TD, flowchart, pie, or other appropriate Mermaid diagram types. Keep diagrams simple and readable. IMPORTANT: Use correct Mermaid syntax for labels on arrows, e.g., `A -->|label| B`. NEVER use invalid syntax like `A -->|label|> B`.
- Reference specific findings from the research
- Be thorough but concise — aim for 800-1200 words
- Do NOT fabricate any information not present in the research
- Each section should flow logically into the next""")

    human = HumanMessage(content=f"""Topic: {state['topic']}

Research Findings:
{research_context}

Total Sources: {len(state.get('sources', []))}
{critic_note}

Write the complete research report:""")

    response = llm.invoke([system, human])

    return {
        "final_report": response.content,
        "current_agent": "writer",
        "log": [
            "Synthesizing all research into final report",
            f"Incorporated {len(state['research_results'])} research sections",
            f"Report generated with {len(state.get('sources', []))} cited sources",
            "✓ Final report complete",
        ],
    }
