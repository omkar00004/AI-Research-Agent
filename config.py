"""Central configuration for model routing, guardrails, and cost estimation.

All instrumentation settings live here so they can be toggled without
touching agent code.  Import with: ``from config import MODEL_CONFIG, …``
"""

# ---------------------------------------------------------------------------
# Model routing per agent role
# ---------------------------------------------------------------------------
# Change the value for any role to switch models.  The key must match the
# ``role`` argument passed to ``get_llm(role=…)`` in each agent module.

MODEL_CONFIG: dict[str, str] = {
    "planner":    "openai/gpt-oss-20b",   # Strong — planning errors cascade
    "researcher": "openai/gpt-oss-20b",       # Cheap — high call volume, mechanical
    "critic":     "openai/gpt-oss-20b",    # Mid-tier — bounded judgment call
    "writer":     "openai/gpt-oss-20b",    # Strong — final synthesis quality
}

# ---------------------------------------------------------------------------
# Retry-loop guardrails
# ---------------------------------------------------------------------------
MAX_RETRIES: int = 2                  # Hard cap on Critic → Researcher retries
MAX_TOKENS_PER_REPORT: int = 100_000  # Total token budget (input + output)
MAX_COST_PER_REPORT: float = 0.10     # USD budget per end-to-end run

# ---------------------------------------------------------------------------
# Groq pricing (USD per 1 M tokens) — update when Groq changes pricing
# ---------------------------------------------------------------------------
COST_RATES: dict[str, dict[str, float]] = {
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "llama-3.1-8b-instant":    {"input": 0.05, "output": 0.08},
}

# ---------------------------------------------------------------------------
# Test topics for benchmarking
# ---------------------------------------------------------------------------
BENCHMARK_TOPICS: list[str] = [
    "Impact of AI agents on software engineering jobs in 2025",
    "Comparison of RAG vs fine-tuning for enterprise LLM applications",
    "State of quantum computing for drug discovery 2025",
    "How central bank digital currencies affect monetary policy",
    "Environmental impact of large-scale data center operations",
    "Evolution of autonomous vehicle regulation in the US and EU",
]
