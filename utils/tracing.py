"""Langfuse tracing helpers for per-report observability.

Creates one Langfuse *trace* per report-generation run (keyed by ``report_id``),
with nested *spans* for each agent node.  Captures tokens, latency, and
estimated cost, and rolls them up into a per-report total.

Usage in agent nodes::

    from utils.tracing import TracingContext
    ctx = TracingContext.from_state(state)
    # … do LLM call …
    ctx.record_llm_call("planner", response, model_name, start_time)
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

# Langfuse is optional — degrade gracefully if not configured
_langfuse_client = None

def _get_langfuse():
    """Lazy singleton for the low-level Langfuse client."""
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client

    pk = os.getenv("LANGFUSE_PUBLIC_KEY")
    sk = os.getenv("LANGFUSE_SECRET_KEY")
    if not pk or not sk:
        return None

    try:
        from langfuse import Langfuse
        _langfuse_client = Langfuse(
            public_key=pk,
            secret_key=sk,
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
        return _langfuse_client
    except Exception:
        return None


def generate_report_id() -> str:
    """Create a unique report_id for a single end-to-end pipeline run."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Per-call metric record
# ---------------------------------------------------------------------------

@dataclass
class NodeMetric:
    """Metrics captured for a single LLM call inside an agent node."""
    agent: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    latency_ms: float = 0.0


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute estimated USD cost using Groq pricing from config."""
    from config import COST_RATES
    rates = COST_RATES.get(model, {})
    if not rates:
        return 0.0
    return (
        input_tokens * rates.get("input", 0) / 1_000_000
        + output_tokens * rates.get("output", 0) / 1_000_000
    )


# ---------------------------------------------------------------------------
# Tracing context — one per pipeline run
# ---------------------------------------------------------------------------

class TracingContext:
    """Manages a Langfuse trace and accumulates metrics for one report run.

    Instantiated once at the start of a pipeline run and threaded through
    agent calls via the shared ``ResearchState``.
    """

    def __init__(self, report_id: str, topic: str = ""):
        self.report_id = report_id
        self.topic = topic
        self.metrics: list[NodeMetric] = []
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_estimated_cost: float = 0.0
        self._trace = None
        self._active_spans: dict[str, Any] = {}

        # Create root Langfuse trace
        lf = _get_langfuse()
        if lf:
            self._trace = lf.trace(
                name="atlas_report",
                id=report_id,
                input={"topic": topic},
                metadata={"report_id": report_id},
                tags=["atlas", "report"],
            )

    # ------------------------------------------------------------------
    # Span management
    # ------------------------------------------------------------------

    def start_span(self, agent: str, retry_count: int = 0, **kwargs) -> float:
        """Open a Langfuse span for an agent node. Returns ``time.time()``."""
        if self._trace:
            span = self._trace.span(
                name=agent,
                metadata={
                    "report_id": self.report_id,
                    "agent_role": agent,
                    "retry_count": retry_count,
                    **kwargs,
                },
            )
            self._active_spans[agent] = span
        return time.time()

    def end_span(self, agent: str, output: Any = None):
        """Close an open span."""
        span = self._active_spans.pop(agent, None)
        if span:
            span.end(output=output)

    def start_child_span(self, parent_agent: str, child_name: str, **kwargs) -> float:
        """Open a child span under an existing agent span."""
        parent = self._active_spans.get(parent_agent)
        if parent:
            child = parent.span(
                name=child_name,
                metadata={"report_id": self.report_id, **kwargs},
            )
            self._active_spans[child_name] = child
        return time.time()

    def end_child_span(self, child_name: str, output: Any = None):
        """Close a child span."""
        span = self._active_spans.pop(child_name, None)
        if span:
            span.end(output=output)

    # ------------------------------------------------------------------
    # LLM call recording
    # ------------------------------------------------------------------

    def record_llm_call(
        self,
        agent: str,
        response: Any,
        model: str,
        start_time: float,
        span_name: Optional[str] = None,
    ) -> NodeMetric:
        """Extract token usage from a LangChain response and record it.

        ``response`` is a ``langchain_core.messages.BaseMessage`` returned
        by ``llm.invoke()``.  LangChain Groq exposes token counts via
        ``response.usage_metadata`` or ``response.response_metadata``.
        """
        elapsed_ms = (time.time() - start_time) * 1000

        # Extract tokens from LangChain response metadata
        input_tokens = 0
        output_tokens = 0

        usage = getattr(response, "usage_metadata", None)
        if usage and isinstance(usage, dict):
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
        else:
            # Fallback: response_metadata from Groq
            resp_meta = getattr(response, "response_metadata", {})
            token_usage = resp_meta.get("token_usage", {})
            input_tokens = token_usage.get("prompt_tokens", 0)
            output_tokens = token_usage.get("completion_tokens", 0)

        cost = _estimate_cost(model, input_tokens, output_tokens)

        metric = NodeMetric(
            agent=agent,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=cost,
            latency_ms=elapsed_ms,
        )

        self.metrics.append(metric)
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_estimated_cost += cost

        # Record as a Langfuse generation on the active span
        target = span_name or agent
        span = self._active_spans.get(target)
        if span:
            span.generation(
                name=f"{agent}_llm_call",
                model=model,
                input={"tokens": input_tokens},
                output={"tokens": output_tokens},
                usage={
                    "input": input_tokens,
                    "output": output_tokens,
                    "total": input_tokens + output_tokens,
                },
                metadata={
                    "estimated_cost_usd": round(cost, 6),
                    "latency_ms": round(elapsed_ms, 1),
                },
            )

        return metric

    # ------------------------------------------------------------------
    # Event logging (guardrail triggers, etc.)
    # ------------------------------------------------------------------

    def log_event(self, name: str, metadata: dict | None = None):
        """Log a distinct event on the current trace (e.g. guardrail hit)."""
        if self._trace:
            self._trace.event(
                name=name,
                metadata={"report_id": self.report_id, **(metadata or {})},
            )

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    def finalize(self, output: Any = None):
        """Close the root trace and flush to Langfuse."""
        if self._trace:
            self._trace.update(
                output=output,
                metadata={
                    "total_input_tokens": self.total_input_tokens,
                    "total_output_tokens": self.total_output_tokens,
                    "total_estimated_cost_usd": round(self.total_estimated_cost, 6),
                    "num_llm_calls": len(self.metrics),
                },
            )
        lf = _get_langfuse()
        if lf:
            lf.flush()

    # ------------------------------------------------------------------
    # Serialization helpers (for state transport)
    # ------------------------------------------------------------------

    def metrics_as_dicts(self) -> list[dict]:
        """Serialize collected metrics for storage in ResearchState."""
        return [
            {
                "agent": m.agent,
                "model": m.model,
                "input_tokens": m.input_tokens,
                "output_tokens": m.output_tokens,
                "estimated_cost": round(m.estimated_cost, 6),
                "latency_ms": round(m.latency_ms, 1),
            }
            for m in self.metrics
        ]


# ---------------------------------------------------------------------------
# Global registry — one context per thread / pipeline run
# ---------------------------------------------------------------------------

_active_contexts: dict[str, TracingContext] = {}


def create_tracing_context(report_id: str, topic: str = "") -> TracingContext:
    """Create and register a new tracing context for a pipeline run."""
    ctx = TracingContext(report_id, topic)
    _active_contexts[report_id] = ctx
    return ctx


def get_tracing_context(report_id: str) -> Optional[TracingContext]:
    """Retrieve the active tracing context for a report_id."""
    return _active_contexts.get(report_id)


def remove_tracing_context(report_id: str):
    """Clean up after a pipeline run completes."""
    _active_contexts.pop(report_id, None)
