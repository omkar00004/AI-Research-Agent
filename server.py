"""FastAPI backend with SSE streaming for the multi-agent research pipeline.

Serves the React frontend as static files and exposes an API endpoint
that streams real-time agent events via Server-Sent Events (SSE).
"""

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

# ---- Langfuse setup ----
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

# ---- Storage for generated reports ----
REPORTS_DIR = Path("generated_reports")
REPORTS_DIR.mkdir(exist_ok=True)

# ---- Storage for conversation history ----
HISTORY_DIR = Path("history")
HISTORY_DIR.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    yield


app = FastAPI(title="Atlas Research API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def run_graph_sync(topic: str, event_queue: asyncio.Queue):
    """Run the LangGraph pipeline synchronously, pushing events to the queue.

    This runs in a thread via asyncio.to_thread so it doesn't block the
    event loop while the graph executes LLM calls.
    """
    from agents.graph import build_graph
    from agents.state import ResearchState

    graph = build_graph()

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

    stream_config = {}
    if langfuse_handler:
        stream_config["callbacks"] = [langfuse_handler]

    completed_agents = []
    final_state = None

    for event in graph.stream(initial_state, config=stream_config, stream_mode="values"):
        current = event.get("current_agent", "")
        logs = event.get("log", [])
        final_state = event

        if not current:
            continue

        # Build subtasks info from research_results if available
        subtasks_data = []
        for r in event.get("research_results", []):
            sources = [
                {
                    "title": s.get("title", ""),
                    "url": s.get("url", ""),
                    "domain": s.get("url", "").split("//")[-1].split("/")[0] if s.get("url") else "",
                }
                for s in r.get("sources", [])
            ]
            subtasks_data.append({
                "id": str(uuid.uuid4())[:8],
                "title": r["subtask"],
                "sources": sources,
                "done": True,
            })

        # Also include planned-but-not-yet-researched subtasks
        researched_titles = {r["subtask"] for r in event.get("research_results", [])}
        for st in event.get("subtasks", []):
            if st not in researched_titles:
                subtasks_data.append({
                    "id": str(uuid.uuid4())[:8],
                    "title": st,
                    "sources": [],
                    "done": False,
                })

        # Build agent statuses
        if current not in completed_agents:
            completed_agents.append(current)

        agent_statuses = {}
        for agent_id in ["planner", "researcher", "critic", "writer"]:
            if agent_id == current:
                agent_statuses[agent_id] = "active"
            elif agent_id in completed_agents:
                agent_statuses[agent_id] = "done"
            else:
                agent_statuses[agent_id] = "idle"

        sse_event = {
            "type": "agent_update",
            "agent": current,
            "statuses": agent_statuses,
            "logs": logs[-6:],
            "subtasks": subtasks_data,
            "report": None,
            "needs_more_research": event.get("needs_more_research", False),
        }

        event_queue.put_nowait(sse_event)

    # Send final report
    if final_state and final_state.get("final_report"):
        # Build final subtasks
        subtasks_data = []
        for r in final_state.get("research_results", []):
            sources = [
                {
                    "title": s.get("title", ""),
                    "url": s.get("url", ""),
                    "domain": s.get("url", "").split("//")[-1].split("/")[0] if s.get("url") else "",
                }
                for s in r.get("sources", [])
            ]
            subtasks_data.append({
                "id": str(uuid.uuid4())[:8],
                "title": r["subtask"],
                "sources": sources,
                "done": True,
            })

        # Generate docx
        docx_filename = None
        try:
            from utils.doc_generator import generate_docx
            docx_bytes = generate_docx(
                topic=topic,
                report_text=final_state["final_report"],
                sources=final_state.get("sources", []),
            )
            docx_filename = f"report_{uuid.uuid4().hex[:8]}.docx"
            (REPORTS_DIR / docx_filename).write_bytes(docx_bytes)
        except Exception:
            pass

        metrics_data = {
            "subtasks": len(final_state.get("subtasks", [])),
            "sources": len(set(s["url"] for s in final_state.get("sources", []))),
            "retries": final_state.get("retry_count", 0),
        }

        all_agent_statuses = {a: "done" for a in ["planner", "researcher", "critic", "writer"]}

        # ---- Auto-save to history ----
        session_id = uuid.uuid4().hex[:12]
        try:
            session_data = {
                "id": session_id,
                "topic": topic,
                "report": final_state["final_report"],
                "subtasks": subtasks_data,
                "metrics": metrics_data,
                "docx_filename": docx_filename,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            (HISTORY_DIR / f"{session_id}.json").write_text(
                json.dumps(session_data, ensure_ascii=False, indent=2)
            )
        except Exception:
            pass

        event_queue.put_nowait({
            "type": "complete",
            "agent": "writer",
            "statuses": all_agent_statuses,
            "logs": ["Report ready."],
            "subtasks": subtasks_data,
            "report": final_state["final_report"],
            "docx_filename": docx_filename,
            "session_id": session_id,
            "metrics": metrics_data,
        })

    event_queue.put_nowait(None)  # Sentinel: stream done


@app.post("/api/research")
async def research(request: Request):
    """Stream the multi-agent research pipeline as SSE events."""
    body = await request.json()
    topic = body.get("topic", "").strip()

    if not topic:
        return {"error": "Topic is required"}, 400

    queue: asyncio.Queue = asyncio.Queue()

    async def event_generator():
        # Launch graph in a background thread
        task = asyncio.create_task(asyncio.to_thread(run_graph_sync, topic, queue))

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            task.cancel()
            raise

        await task  # Ensure the thread has finished

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/download/{filename}")
async def download_report(filename: str):
    """Download a generated .docx report."""
    filepath = REPORTS_DIR / filename
    if not filepath.exists():
        return {"error": "File not found"}, 404
    return FileResponse(
        path=filepath,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    missing_keys = []
    if not os.getenv("GROQ_API_KEY"):
        missing_keys.append("GROQ_API_KEY")
    if not os.getenv("TAVILY_API_KEY"):
        missing_keys.append("TAVILY_API_KEY")
    return {
        "status": "ok" if not missing_keys else "missing_keys",
        "missing_keys": missing_keys,
        "langfuse_active": langfuse_handler is not None,
    }


# ---- History API ----

@app.get("/api/history")
async def list_history():
    """List all saved research sessions, sorted by most recent first."""
    sessions = []
    for f in HISTORY_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            sessions.append({
                "id": data["id"],
                "topic": data["topic"],
                "created_at": data["created_at"],
                "metrics": data.get("metrics"),
            })
        except Exception:
            continue
    sessions.sort(key=lambda s: s["created_at"], reverse=True)
    return sessions


@app.get("/api/history/{session_id}")
async def get_history(session_id: str):
    """Get full session data for a past conversation."""
    filepath = HISTORY_DIR / f"{session_id}.json"
    if not filepath.exists():
        return {"error": "Session not found"}, 404
    data = json.loads(filepath.read_text())
    return data


@app.delete("/api/history/{session_id}")
async def delete_history(session_id: str):
    """Delete a saved session."""
    filepath = HISTORY_DIR / f"{session_id}.json"
    if not filepath.exists():
        return {"error": "Session not found"}, 404
    filepath.unlink()
    return {"status": "deleted"}


# ---- Serve React frontend ----
FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"

if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve the React SPA — any non-API path returns index.html."""
        file_path = FRONTEND_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
