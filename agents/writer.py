from langchain_core.messages import SystemMessage, HumanMessage
from agents.state import ResearchState
import re
import os
import time
import unicodedata
from utils.llm import get_llm
from utils.tracing import get_tracing_context


def fix_mermaid_syntax(text: str) -> str:
    """
    Post-process LLM output to fix common Mermaid syntax errors.
    Handles the most frequent hallucination: -->|label|> instead of -->|label|
    Also fixes: ---->|label|>, -..->|label|>, ===>|label|>
    """
    # Fix -->|label|> B  =>  -->|label| B
    text = re.sub(r'(\-+>)\|([^|]+)\|>', r'\1|\2|', text)

    # Fix === arrow variant: ==>|label|> => ==>|label|
    text = re.sub(r'(=+>)\|([^|]+)\|>', r'\1|\2|', text)

    # Fix dotted arrow: -.->|label|> => -.->|label|
    text = re.sub(r'(-\.->)\|([^|]+)\|>', r'\1|\2|', text)

    # Fix nodes with special chars that break Mermaid - wrap in quotes if needed
    # e.g. A[Some & Thing] => A["Some & Thing"]
    text = re.sub(r'\[([^\]]*&[^\]]*)\]', lambda m: '["' + m.group(1) + '"]', text)

    return text


def normalize_unicode(text: str) -> str:
    """Normalize non-standard Unicode characters that break frontend rendering.

    LLMs often copy non-breaking spaces, en/em-dashes, and non-breaking
    hyphens verbatim from scraped web sources. These cause layout glitches
    in the React markdown renderer (headers overflow, words refuse to wrap).
    This function replaces them with their standard ASCII equivalents.
    """
    # Non-breaking spaces and thin/narrow no-break spaces → standard space
    text = text.replace("\u00A0", " ")   # NO-BREAK SPACE
    text = text.replace("\u202F", " ")   # NARROW NO-BREAK SPACE
    text = text.replace("\u2007", " ")   # FIGURE SPACE
    text = text.replace("\u2060", "")    # WORD JOINER (zero-width, invisible)

    # Non-breaking hyphen → regular hyphen
    text = text.replace("\u2011", "-")   # NON-BREAKING HYPHEN

    # En-dash / Em-dash → regular hyphen (keeps readability in plain text)
    text = text.replace("\u2013", "-")   # EN DASH
    text = text.replace("\u2014", "--")  # EM DASH

    # Smart / curly quotes → straight ASCII quotes (avoids glyph-box glitches)
    text = text.replace("\u2018", "'")   # LEFT SINGLE QUOTATION MARK
    text = text.replace("\u2019", "'")   # RIGHT SINGLE QUOTATION MARK
    text = text.replace("\u201C", '"')   # LEFT DOUBLE QUOTATION MARK
    text = text.replace("\u201D", '"')   # RIGHT DOUBLE QUOTATION MARK

    # Horizontal ellipsis → three dots
    text = text.replace("\u2026", "...")  # HORIZONTAL ELLIPSIS

    # Unicode bullet variants → standard hyphen-bullet (consistent with prompt)
    text = text.replace("\u2022", "-")   # BULLET
    text = text.replace("\u2023", "-")   # TRIANGULAR BULLET
    text = text.replace("\u25CF", "-")   # BLACK CIRCLE

    return text


def writer_agent(state: ResearchState) -> dict:
    llm = get_llm(role="writer", temperature=0.4)

    # Resolve model name for metrics
    try:
        from config import MODEL_CONFIG
        model_name = MODEL_CONFIG.get("writer", "llama-3.3-70b-versatile")
    except ImportError:
        model_name = "llama-3.3-70b-versatile"

    # Get tracing context
    ctx = get_tracing_context(state.get("report_id", ""))
    if ctx:
        ctx.start_span("writer", retry_count=state.get("retry_count", 0))

    research_content = "\n\n".join([
        f"## {r['subtask']}\n{r['synthesis']}"
        for r in state["research_results"]
    ])

    critic_note = ""
    if state.get("critique"):
        critic_note = f"\n\nCritic feedback to incorporate: {state['critique']}"

    # If guardrails were hit, note it for the writer
    guardrail_note = ""
    if state.get("budget_exceeded"):
        guardrail_note += "\n\nNote: The research budget was exceeded, so work with the available research."
    if state.get("max_retries_reached"):
        guardrail_note += "\n\nNote: Maximum research retries were reached. Synthesize the best available findings."

    system = SystemMessage(content="""You are a senior analyst at a top-tier consulting firm.
Write a comprehensive, professional research report based on the research provided.

Structure the report as follows:
1. EXECUTIVE SUMMARY (3-4 sentences, key takeaways)
2. INTRODUCTION (context and why this topic matters)
3. FINDINGS (one section per research subtask, with clear headers using ###)
4. KEY INSIGHTS (3-5 bullet points of the most important takeaways)
5. CONCLUSION (summary and forward-looking statement)

Formatting rules:
- Use ## for main sections, ### for subsections
- Use **bold** for key terms
- Use bullet points only in KEY INSIGHTS
- Write 800-1000 words total

Mermaid diagram rules (CRITICAL - follow exactly):
- When the topic involves a process, workflow, or system, include ONE Mermaid diagram
- Use ```mermaid code blocks
- Arrow syntax: A -->|label| B  (NEVER use -->|label|> - the > after closing pipe is INVALID)
- Node syntax: A[Label text] or A(Label text) - keep labels short, no special characters
- Stick to: graph TD, graph LR, flowchart TD, or pie chart types only
- Example of CORRECT syntax:
  graph TD
    A[Start] -->|Step 1| B[Process]
    B -->|Step 2| C[End]
- Example of WRONG syntax (never do this):
  A -->|label|> B   ← INVALID, the > after | breaks rendering""")

    human = HumanMessage(content=f"""Topic: {state['topic']}

Research findings:
{research_content}
{critic_note}
{guardrail_note}

Write the full professional report:""")

    llm_start = time.time()
    response = llm.invoke([system, human])

    # Record LLM metrics
    if ctx:
        ctx.record_llm_call("writer", response, model_name, llm_start)
        ctx.end_span("writer", output={"report_length": len(response.content)})

    # Post-process: fix Mermaid syntax, then normalize Unicode characters
    # that cause text-breaking / layout glitches in the frontend renderer
    cleaned_report = fix_mermaid_syntax(response.content)
    cleaned_report = normalize_unicode(cleaned_report)

    result = {
        "final_report": cleaned_report,
        "current_agent": "writer",
        "log": [
            "Writer synthesized all research into final report",
            f"Incorporated {len(state['research_results'])} research sections",
            "Applied Mermaid syntax validation",
            "Report complete",
        ],
    }

    # Propagate final accumulated metrics
    if ctx:
        result["total_input_tokens"] = ctx.total_input_tokens
        result["total_output_tokens"] = ctx.total_output_tokens
        result["total_estimated_cost"] = ctx.total_estimated_cost
        result["agent_metrics"] = ctx.metrics_as_dicts()

    return result