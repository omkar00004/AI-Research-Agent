"""Streamlit UI with live agent streaming, metrics, and download button.

Integrates Langfuse for LLM traceability and observability.
"""

import streamlit as st
import os
from dotenv import load_dotenv
from agents.graph import build_graph
from agents.state import ResearchState
from utils.doc_generator import generate_docx

load_dotenv()

# ---- LANGFUSE SETUP ----
langfuse_handler = None
if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
    try:
        from langfuse.callback import CallbackHandler
        langfuse_handler = CallbackHandler(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
    except ImportError:
        pass

# ---- PAGE CONFIG ----
st.set_page_config(
    page_title="AI Research Agent",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---- CUSTOM CSS ----
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    .main-header {
        background: linear-gradient(135deg, #1B4F8A 0%, #2E86C1 100%);
        padding: 2.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 2rem;
        color: white;
    }

    .main-header h1 {
        font-size: 2rem;
        font-weight: 700;
        margin: 0;
        color: white;
    }

    .main-header p {
        font-size: 1rem;
        opacity: 0.85;
        margin: 0.5rem 0 0 0;
        color: white;
    }

    .agent-card {
        border-radius: 10px;
        padding: 1rem 1.2rem;
        margin-bottom: 0.75rem;
        border-left: 4px solid #1B4F8A;
        background: #F0F6FF;
        animation: fadeIn 0.4s ease-in;
    }

    .agent-card.active {
        border-left: 4px solid #27AE60;
        background: #F0FFF4;
    }

    .agent-card.done {
        border-left: 4px solid #1B4F8A;
        background: #F0F6FF;
        opacity: 0.85;
    }

    .agent-label {
        font-weight: 600;
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #1B4F8A;
        margin-bottom: 0.3rem;
    }

    .log-line {
        font-size: 0.88rem;
        color: #333;
        padding: 0.15rem 0;
        line-height: 1.5;
    }

    .status-badge {
        display: inline-block;
        padding: 0.2rem 0.7rem;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
    }

    .badge-running { background: #FFF3CD; color: #856404; }
    .badge-done { background: #D1ECE1; color: #155724; }
    .badge-waiting { background: #E2E8F0; color: #64748B; }

    .report-container {
        background: white;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 2rem;
        margin-top: 1rem;
        box-shadow: 0 2px 12px rgba(0,0,0,0.06);
    }

    .metric-card {
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
    }

    .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #1B4F8A;
    }

    .metric-label {
        font-size: 0.78rem;
        color: #64748B;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-top: 0.2rem;
    }

    .stButton > button {
        background: linear-gradient(135deg, #1B4F8A, #2E86C1);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 0.6rem 2rem;
        font-weight: 600;
        font-size: 1rem;
        width: 100%;
        transition: all 0.2s;
    }

    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(27, 79, 138, 0.3);
    }

    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(6px); }
        to { opacity: 1; transform: translateY(0); }
    }

    .divider {
        border: none;
        border-top: 1px solid #E2E8F0;
        margin: 1.5rem 0;
    }
</style>
""", unsafe_allow_html=True)


# ---- AGENT CONFIG ----
AGENTS = {
    "planner": {"icon": "🗺️", "name": "Planner Agent", "desc": "Breaking topic into research subtasks"},
    "researcher": {"icon": "🔍", "name": "Research Agent", "desc": "Searching and synthesizing sources"},
    "critic": {"icon": "🧐", "name": "Critic Agent", "desc": "Evaluating research quality"},
    "writer": {"icon": "✍️", "name": "Writer Agent", "desc": "Composing the final report"},
}


def check_api_keys():
    """Return a list of missing required API key names."""
    missing = []
    if not os.getenv("GROQ_API_KEY"):
        missing.append("GROQ_API_KEY")
    if not os.getenv("TAVILY_API_KEY"):
        missing.append("TAVILY_API_KEY")
    return missing


# ---- MAIN UI ----
st.markdown("""
<div class="main-header">
    <h1>🔬 AI Multi-Agent Research System</h1>
    <p>4 specialized AI agents collaborate to research any topic and generate a professional report</p>
</div>
""", unsafe_allow_html=True)

# API key check
missing_keys = check_api_keys()
if missing_keys:
    st.error(f"Missing API keys: {', '.join(missing_keys)}. Add them to your .env file.")
    st.code("\n".join([f"{k}=your_key_here" for k in missing_keys]), language="bash")
    st.stop()

# ---- INPUT SECTION ----
col1, col2 = st.columns([3, 1])

with col1:
    topic = st.text_input(
        "Research Topic",
        placeholder="e.g. Impact of AI agents on software engineering jobs in 2025",
        help="Be specific for better results",
        label_visibility="collapsed",
    )

with col2:
    run_btn = st.button("🚀 Generate Report", use_container_width=True)

st.markdown('<hr class="divider">', unsafe_allow_html=True)

# ---- AGENT PIPELINE DISPLAY ----
if run_btn and topic:
    # Layout: agent log left, report right
    left_col, right_col = st.columns([1, 2])

    with left_col:
        st.markdown("### 🤖 Agent Pipeline")

        agent_placeholders = {}
        for key, info in AGENTS.items():
            st.markdown(f"""
            <div class="agent-card" id="card-{key}">
                <div class="agent-label">{info['icon']} {info['name']}
                    <span class="status-badge badge-waiting">Waiting</span>
                </div>
                <div class="log-line" style="color:#94A3B8;">{info['desc']}</div>
            </div>
            """, unsafe_allow_html=True)
            agent_placeholders[key] = st.empty()

    with right_col:
        st.markdown("### 📄 Live Report")
        report_placeholder = st.empty()
        download_placeholder = st.empty()

    # ---- RUN THE GRAPH ----
    graph = build_graph()
    completed_agents = []
    final_state = None

    initial_state: ResearchState = {
        "topic": topic,
        "subtasks": [],
        "research_results": [],
        "critique": None,
        "needs_more_research": False,
        "retry_count": 0,
        "final_report": None,
        "sources": [],
        "current_agent": "",
        "log": [],
    }

    # Stream config with optional Langfuse callback
    stream_config = {}
    if langfuse_handler:
        stream_config["callbacks"] = [langfuse_handler]

    for event in graph.stream(initial_state, config=stream_config, stream_mode="values"):
        current = event.get("current_agent", "")
        logs = event.get("log", [])
        final_state = event

        if not current:
            continue

        info = AGENTS.get(current, {})

        # Update active agent card
        log_html = "".join([f'<div class="log-line">• {l}</div>' for l in logs[-4:]])

        agent_placeholders[current].markdown(f"""
        <div class="agent-card active">
            <div class="agent-label">{info.get('icon','')} {info.get('name', current)}
                <span class="status-badge badge-running">Running</span>
            </div>
            {log_html}
        </div>
        """, unsafe_allow_html=True)

        # Mark previous as done
        for prev in completed_agents:
            prev_info = AGENTS.get(prev, {})
            agent_placeholders[prev].markdown(f"""
            <div class="agent-card done">
                <div class="agent-label">{prev_info.get('icon','')} {prev_info.get('name', prev)}
                    <span class="status-badge badge-done">Done ✓</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

        # Show live report preview as writer works
        if current == "writer" and event.get("final_report"):
            report_placeholder.markdown(f"""
            <div class="report-container">
                {event['final_report'][:1200]}...
            </div>
            """, unsafe_allow_html=True)

        if current not in completed_agents:
            completed_agents.append(current)

    # ---- FINAL STATE ----
    if final_state and final_state.get("final_report"):

        # Mark all done
        for agent_key in completed_agents:
            prev_info = AGENTS.get(agent_key, {})
            agent_placeholders[agent_key].markdown(f"""
            <div class="agent-card done">
                <div class="agent-label">{prev_info.get('icon','')} {prev_info.get('name', agent_key)}
                    <span class="status-badge badge-done">Done ✓</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

        # Metrics
        with left_col:
            st.markdown('<hr class="divider">', unsafe_allow_html=True)
            st.markdown("### 📊 Run Summary")
            m1, m2, m3 = st.columns(3)
            with m1:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-value">{len(final_state.get('subtasks', []))}</div>
                    <div class="metric-label">Subtasks</div></div>""", unsafe_allow_html=True)
            with m2:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-value">{len(set(s['url'] for s in final_state.get('sources', [])))} </div>
                    <div class="metric-label">Sources</div></div>""", unsafe_allow_html=True)
            with m3:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-value">{final_state.get('retry_count', 0)}</div>
                    <div class="metric-label">Retries</div></div>""", unsafe_allow_html=True)

        # Full report display
        with right_col:
            report_placeholder.markdown(f"""
            <div class="report-container">
                {final_state['final_report']}
            </div>
            """, unsafe_allow_html=True)

            # Generate docx
            docx_bytes = generate_docx(
                topic=topic,
                report_text=final_state["final_report"],
                sources=final_state.get("sources", []),
            )

            download_placeholder.download_button(
                label="📥 Download Professional Report (.docx)",
                data=docx_bytes,
                file_name=f"research_report_{topic[:40].replace(' ', '_')}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )

            st.success("Report generated successfully!")

elif run_btn and not topic:
    st.warning("Please enter a research topic first.")

# ---- SIDEBAR INFO ----
with st.sidebar:
    st.markdown("### ⚙️ How It Works")
    st.markdown("""
    **4 Specialized Agents:**

    🗺️ **Planner** - Breaks your topic into focused research subtasks

    🔍 **Researcher** - Searches the web via Tavily and synthesizes findings per subtask

    🧐 **Critic** - Evaluates research quality, triggers retry if insufficient

    ✍️ **Writer** - Synthesizes everything into a structured professional report

    ---
    **Output:**
    Downloadable `.docx` report with executive summary, findings, key insights, and cited sources.

    ---
    **Stack:** LangGraph · Groq · Tavily · Langfuse · Streamlit
    """)

    st.markdown("### 🔑 API Keys Required")
    st.code("GROQ_API_KEY=...\nTAVILY_API_KEY=...", language="bash")

    if langfuse_handler:
        st.success("✅ Langfuse tracing active")
    else:
        st.info("ℹ️ Langfuse tracing disabled (set keys to enable)")
