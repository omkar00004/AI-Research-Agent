"""Run Atlas conditions (B0/B1/B2/B2_r1) for one subject, with a query-level search cache."""
from __future__ import annotations

import contextvars
import functools
import json
import re
import time
import uuid
from pathlib import Path

from common import CACHE, TAVILY_USD_PER_ADVANCED_SEARCH, est_cost, sha, write_json, read_json

import config  # Atlas config (flags read at call time)

# ---------------------------------------------------------------------------
# Search cache: key = (query, params, occurrence index of that query within the run).
# Re-running a subject replays identical results (deterministic), while a retry that
# re-issues the same query in the same run gets its own live call, as in real Atlas.
# ---------------------------------------------------------------------------
_CURRENT: contextvars.ContextVar = contextvars.ContextVar("eval_run", default=None)


class RunCtx:
    def __init__(self):
        self.occ: dict[str, int] = {}
        self.calls: list[dict] = []   # {query, cached, latency_s, orig_latency_s, ok}
        self.retrieved: list[dict] = []


class CachedTavily:
    def __init__(self, api_key=None):
        from tavily import TavilyClient
        self._real = TavilyClient(api_key=api_key)

    def search(self, **kw):
        run: RunCtx = _CURRENT.get()
        q = kw.get("query", "")
        pk = sha(json.dumps({k: v for k, v in kw.items() if k != "query"}, sort_keys=True))
        base = sha(q + "|" + pk)
        n = run.occ.get(base, 0)
        run.occ[base] = n + 1
        path = CACHE / "search" / f"{base}_{n}.json"
        t0 = time.time()
        if path.exists():
            d = read_json(path)
            run.calls.append({"query": q, "cached": True, "latency_s": time.time() - t0,
                              "orig_latency_s": d["latency_s"], "ok": True})
            resp = d["response"]
        else:
            try:
                resp = self._real.search(**kw)
            except Exception:
                run.calls.append({"query": q, "cached": False, "latency_s": time.time() - t0,
                                  "orig_latency_s": time.time() - t0, "ok": False})
                raise
            lat = time.time() - t0
            write_json(path, {"query": q, "params": kw, "latency_s": lat, "response": resp})
            run.calls.append({"query": q, "cached": False, "latency_s": lat, "orig_latency_s": lat, "ok": True})
        for r in resp.get("results", []):
            run.retrieved.append({"url": r.get("url", ""), "title": r.get("title", ""),
                                  "snippet": (r.get("content") or "")[:1500]})
        return resp


_patched = False


def _patch_once():
    """Install the cache + more tolerant LLM retry settings (does not change prompts or models)."""
    global _patched
    if _patched:
        return
    import agents.researcher as ar
    ar.TavilyClient = CachedTavily
    import utils.llm as ul
    ul.ChatGroq = functools.partial(ul.ChatGroq, max_retries=8, timeout=240)
    import langchain_openai
    langchain_openai.ChatOpenAI = functools.partial(langchain_openai.ChatOpenAI, max_retries=8, timeout=240)
    _patched = True


def set_condition(cond: str):
    """Configure Atlas flags for a condition (call before building the graph)."""
    import agents.critic as ac
    config.CITE_MODE = True                        # all conditions are asked for inline citations
    config.ENABLE_CRITIC = cond in ("B2", "B2_r1")
    mr = 1 if cond == "B2_r1" else 2
    config.MAX_RETRIES = mr
    ac.MAX_RETRIES = mr                            # critic.py imported the constant by value


def _usage(resp) -> tuple[int, int]:
    u = getattr(resp, "usage_metadata", None)
    if u:
        return u.get("input_tokens", 0), u.get("output_tokens", 0)
    tu = (getattr(resp, "response_metadata", {}) or {}).get("token_usage", {})
    return tu.get("prompt_tokens", 0), tu.get("completion_tokens", 0)


# Verbatim copy of the writer's system prompt body (checked against agents/writer.py at startup).
WRITER_BODY_PREFIX = "You are a senior analyst at a top-tier consulting firm.\nWrite a comprehensive, professional research report based on the research provided."


def run_b0(topic: str) -> dict:
    """No retrieval: one call with the writer's model and system prompt."""
    from langchain_core.messages import SystemMessage, HumanMessage
    from utils.llm import get_llm
    import inspect
    import agents.writer as aw
    src = inspect.getsource(aw.writer_agent)
    m = re.search(r'SystemMessage\(content="""(.*?)""" \+', src, flags=re.S)
    assert m, "could not locate writer system prompt"
    system = m.group(1) + config.CITE_WRITER_SUFFIX
    human = f"""Topic: {topic}

No research findings or search tool are available. Write the report from your own knowledge.
If you cite a source, cite only a real, specific page URL you are confident exists; otherwise leave the claim uncited.

Write the full professional report:"""
    model = config.MODEL_CONFIG["writer"]
    llm = get_llm(role="writer", temperature=0.4)
    t0 = time.time()
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
    lat = time.time() - t0
    tin, tout = _usage(resp)
    from agents.writer import fix_mermaid_syntax, normalize_unicode
    report = normalize_unicode(fix_mermaid_syntax(resp.content))
    return {"report": report, "sources": [], "retrieved": [], "search_calls": [], "retry_loops": 0,
            "tokens": {"in": tin, "out": tout}, "agent_tokens": {"writer": {"in": tin, "out": tout}},
            "llm_cost_usd": est_cost(model, tin, tout), "latency_s": lat, "subtasks": [], "models": {"writer": model},
            "flags": {}}


def run_atlas(topic: str, cond: str) -> dict:
    from agents.graph import build_graph
    from utils.tracing import generate_report_id, create_tracing_context, remove_tracing_context
    set_condition(cond)
    graph = build_graph()
    rid = generate_report_id()
    ctx = create_tracing_context(rid, topic)
    run = RunCtx()
    tok = _CURRENT.set(run)
    state = {"topic": topic, "subtasks": [], "research_results": [], "critique": None, "needs_more_research": False,
             "retry_count": 0, "final_report": None, "sources": [], "current_agent": "", "log": [], "report_id": rid,
             "total_input_tokens": 0, "total_output_tokens": 0, "total_estimated_cost": 0.0, "agent_metrics": [],
             "budget_exceeded": False, "max_retries_reached": False}
    t0 = time.time()
    final = None
    try:
        # stream_mode="values" mirrors server.py; copy_context so nodes see the run context
        cvctx = contextvars.copy_context()
        for ev in cvctx.run(lambda: list(graph.stream(state, stream_mode="values"))):
            if ev.get("current_agent"):
                final = ev
    finally:
        _CURRENT.reset(tok)
    lat = time.time() - t0
    try:
        agent_tokens, cost, tin, tout, models = {}, 0.0, 0, 0, {}
        for m in ctx.metrics:
            a = agent_tokens.setdefault(m.agent, {"in": 0, "out": 0})
            a["in"] += m.input_tokens
            a["out"] += m.output_tokens
            cost += est_cost(m.model, m.input_tokens, m.output_tokens)
            tin += m.input_tokens
            tout += m.output_tokens
            models[m.agent] = m.model
    finally:
        remove_tracing_context(rid)
    if not final or not final.get("final_report"):
        raise RuntimeError("pipeline produced no report")
    # latency with searches at their original (uncached) latency
    adj = sum(c["orig_latency_s"] - c["latency_s"] for c in run.calls if c["cached"])
    return {"report": final["final_report"], "sources": final.get("sources", []), "retrieved": run.retrieved,
            "search_calls": run.calls, "retry_loops": final.get("retry_count", 0),
            "tokens": {"in": tin, "out": tout}, "agent_tokens": agent_tokens, "llm_cost_usd": cost,
            "latency_s": lat, "latency_uncached_est_s": lat + adj, "subtasks": final.get("subtasks", []),
            "models": models, "critique": final.get("critique"),
            "flags": {"max_retries_reached": final.get("max_retries_reached"), "budget_exceeded": final.get("budget_exceeded")}}


def run_condition(topic: str, cond: str) -> dict:
    _patch_once()
    set_condition(cond)
    out = run_b0(topic) if cond == "B0" else run_atlas(topic, cond)
    n_search = len(out["search_calls"])
    out["n_search_calls"] = n_search
    out["search_cost_usd"] = n_search * TAVILY_USD_PER_ADVANCED_SEARCH
    out["total_cost_usd"] = out["llm_cost_usd"] + out["search_cost_usd"]
    out.setdefault("latency_uncached_est_s", out["latency_s"])
    return out
