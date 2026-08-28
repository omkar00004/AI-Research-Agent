#!/usr/bin/env python3
"""Benchmark test harness for Atlas pipeline instrumentation.

Runs a set of test topics through the pipeline twice:
  1. Pre-routing  — all agents use the 70B model
  2. Post-routing — Researcher uses the cheap 8B model (config default)

Collects per-run metrics and writes results to ``tests/results/benchmark_results.json``.

Usage::

    # From the project root (with venv activated):
    python tests/test_topics.py

    # Run a subset of topics:
    python tests/test_topics.py --topics 2

    # Skip pre-routing baseline (only run post-routing):
    python tests/test_topics.py --skip-baseline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on the path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")


def run_single_topic(topic: str, model_override: dict | None = None) -> dict:
    """Run one topic through the pipeline and collect metrics.

    Args:
        topic: Research topic string.
        model_override: If provided, temporarily patches ``config.MODEL_CONFIG``
                        for this run (e.g. to force all models to 70B for baseline).

    Returns:
        A dict with all collected metrics for this run.
    """
    import config
    from agents.graph import build_graph
    from agents.state import ResearchState
    from utils.tracing import (
        generate_report_id, create_tracing_context, remove_tracing_context,
    )

    # Patch model config if override is provided
    original_config = dict(config.MODEL_CONFIG)
    if model_override:
        config.MODEL_CONFIG.update(model_override)

    try:
        graph = build_graph()
        report_id = generate_report_id()
        ctx = create_tracing_context(report_id, topic)

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
            "report_id": report_id,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_estimated_cost": 0.0,
            "agent_metrics": [],
            "budget_exceeded": False,
            "max_retries_reached": False,
        }

        start_time = time.time()
        final_state = None

        for event in graph.stream(initial_state, stream_mode="values"):
            if event.get("current_agent"):
                final_state = event

        elapsed = time.time() - start_time

        # Finalize trace
        if ctx:
            ctx.finalize(output={
                "report_length": len(final_state.get("final_report", "")) if final_state else 0,
                "retries": final_state.get("retry_count", 0) if final_state else 0,
            })

        # Build cost breakdown by agent role
        cost_by_agent: dict[str, float] = {}
        tokens_by_agent: dict[str, int] = {}
        if ctx:
            for m in ctx.metrics:
                cost_by_agent[m.agent] = cost_by_agent.get(m.agent, 0) + m.estimated_cost
                tokens_by_agent[m.agent] = (
                    tokens_by_agent.get(m.agent, 0) + m.input_tokens + m.output_tokens
                )

        # Run eval if we have a report
        eval_result = None
        if final_state and final_state.get("final_report"):
            try:
                from utils.eval import run_evaluation
                results_dir = PROJECT_ROOT / "tests" / "results"
                eval_result = run_evaluation(
                    report_id=report_id,
                    report_text=final_state["final_report"],
                    subtasks=final_state.get("subtasks", []),
                    topic=topic,
                    output_dir=results_dir,
                )
            except Exception as e:
                eval_result = {"error": str(e)}

        result = {
            "report_id": report_id,
            "topic": topic,
            "model_config": dict(config.MODEL_CONFIG),
            "total_cost": round(ctx.total_estimated_cost, 6) if ctx else 0,
            "total_input_tokens": ctx.total_input_tokens if ctx else 0,
            "total_output_tokens": ctx.total_output_tokens if ctx else 0,
            "cost_by_agent": {k: round(v, 6) for k, v in cost_by_agent.items()},
            "tokens_by_agent": tokens_by_agent,
            "retry_count": final_state.get("retry_count", 0) if final_state else 0,
            "max_retries_reached": final_state.get("max_retries_reached", False) if final_state else False,
            "budget_exceeded": final_state.get("budget_exceeded", False) if final_state else False,
            "latency_s": round(elapsed, 1),
            "subtasks_count": len(final_state.get("subtasks", [])) if final_state else 0,
            "sources_count": len(set(s["url"] for s in final_state.get("sources", []))) if final_state else 0,
            "report_length": len(final_state.get("final_report", "")) if final_state else 0,
            "completeness_score": (eval_result or {}).get("completeness", {}).get("score"),
            "citation_coverage_pct": (eval_result or {}).get("citation_coverage", {}).get("coverage_pct"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        remove_tracing_context(report_id)
        return result

    finally:
        # Restore original config
        config.MODEL_CONFIG.update(original_config)


def print_results_table(results: list[dict], label: str):
    """Pretty-print a summary table to stdout."""
    print(f"\n{'='*90}")
    print(f"  {label}")
    print(f"{'='*90}")
    print(f"{'Topic':<55} {'Cost':>8} {'Tokens':>8} {'Retries':>7} {'Time':>6} {'Score':>5}")
    print(f"{'-'*55} {'-'*8} {'-'*8} {'-'*7} {'-'*6} {'-'*5}")

    total_cost = 0
    total_retries = 0

    for r in results:
        topic_short = r["topic"][:53] + ".." if len(r["topic"]) > 55 else r["topic"]
        score = r.get("completeness_score")
        score_str = f"{score}/10" if score is not None else "N/A"
        total_tokens = r["total_input_tokens"] + r["total_output_tokens"]
        print(
            f"{topic_short:<55} "
            f"${r['total_cost']:.4f} "
            f"{total_tokens:>7,} "
            f"{r['retry_count']:>7} "
            f"{r['latency_s']:>5.0f}s "
            f"{score_str:>5}"
        )
        total_cost += r["total_cost"]
        total_retries += r["retry_count"]

    n = len(results)
    print(f"{'-'*55} {'-'*8} {'-'*8} {'-'*7} {'-'*6} {'-'*5}")
    print(f"{'TOTALS/AVERAGES':<55} ${total_cost:.4f} {'':>8} {total_retries/n:>7.1f} {'':>6} {'':>5}")

    # Cost breakdown by agent
    print(f"\n  Cost Breakdown by Agent Role:")
    agent_costs: dict[str, float] = {}
    for r in results:
        for agent, cost in r.get("cost_by_agent", {}).items():
            agent_costs[agent] = agent_costs.get(agent, 0) + cost
    for agent in ["planner", "researcher", "critic", "writer"]:
        if agent in agent_costs:
            print(f"    {agent:<15} ${agent_costs[agent]:.4f}  (avg ${agent_costs[agent]/n:.4f}/report)")

    # Cap hit rates
    cap_hits = sum(1 for r in results if r.get("max_retries_reached"))
    budget_hits = sum(1 for r in results if r.get("budget_exceeded"))
    print(f"\n  Retry cap hit rate:  {cap_hits}/{n} ({cap_hits/n*100:.0f}%)")
    print(f"  Budget exceeded:    {budget_hits}/{n} ({budget_hits/n*100:.0f}%)")
    print()


def main():
    parser = argparse.ArgumentParser(description="Atlas Pipeline Benchmark")
    parser.add_argument("--topics", type=int, default=None,
                        help="Number of topics to run (default: all 6)")
    parser.add_argument("--skip-baseline", action="store_true",
                        help="Skip pre-routing baseline runs")
    args = parser.parse_args()

    from config import BENCHMARK_TOPICS

    topics = BENCHMARK_TOPICS[:args.topics] if args.topics else BENCHMARK_TOPICS
    results_dir = PROJECT_ROOT / "tests" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    all_results = {
        "pre_routing": [],
        "post_routing": [],
        "run_date": datetime.now(timezone.utc).isoformat(),
    }

    # ---- Pre-routing baseline (all 70B) ----
    if not args.skip_baseline:
        print("\n🔬 Phase 1: Pre-routing baseline (all agents on 70B)")
        print("=" * 60)
        baseline_override = {
            "planner": "llama-3.3-70b-versatile",
            "researcher": "llama-3.3-70b-versatile",
            "critic": "llama-3.3-70b-versatile",
            "writer": "llama-3.3-70b-versatile",
        }
        for i, topic in enumerate(topics, 1):
            print(f"\n[{i}/{len(topics)}] {topic}")
            try:
                result = run_single_topic(topic, model_override=baseline_override)
                all_results["pre_routing"].append(result)
                print(f"  ✓ Cost: ${result['total_cost']:.4f} | "
                      f"Retries: {result['retry_count']} | "
                      f"Time: {result['latency_s']:.0f}s")
            except Exception as e:
                print(f"  ✗ Error: {e}")
                all_results["pre_routing"].append({
                    "topic": topic, "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        print_results_table(
            [r for r in all_results["pre_routing"] if "error" not in r],
            "PRE-ROUTING BASELINE (All 70B)"
        )

    # ---- Post-routing (Researcher on 8B) ----
    print("\n🚀 Phase 2: Post-routing (Researcher on 8B instant)")
    print("=" * 60)
    for i, topic in enumerate(topics, 1):
        print(f"\n[{i}/{len(topics)}] {topic}")
        try:
            # Uses config defaults (researcher = 8B)
            result = run_single_topic(topic)
            all_results["post_routing"].append(result)
            print(f"  ✓ Cost: ${result['total_cost']:.4f} | "
                  f"Retries: {result['retry_count']} | "
                  f"Time: {result['latency_s']:.0f}s")
        except Exception as e:
            print(f"  ✗ Error: {e}")
            all_results["post_routing"].append({
                "topic": topic, "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    print_results_table(
        [r for r in all_results["post_routing"] if "error" not in r],
        "POST-ROUTING (Researcher on 8B)"
    )

    # ---- Comparison ----
    pre = [r for r in all_results["pre_routing"] if "error" not in r]
    post = [r for r in all_results["post_routing"] if "error" not in r]

    if pre and post:
        pre_avg = sum(r["total_cost"] for r in pre) / len(pre)
        post_avg = sum(r["total_cost"] for r in post) / len(post)
        savings = ((pre_avg - post_avg) / pre_avg) * 100 if pre_avg > 0 else 0

        print(f"\n{'='*60}")
        print(f"  COMPARISON")
        print(f"{'='*60}")
        print(f"  Pre-routing avg cost:   ${pre_avg:.4f}/report")
        print(f"  Post-routing avg cost:  ${post_avg:.4f}/report")
        print(f"  Cost savings:           {savings:.1f}%")
        print()

    # Save raw results
    results_path = results_dir / "benchmark_results.json"
    results_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"📊 Full results saved to: {results_path}")


if __name__ == "__main__":
    main()
