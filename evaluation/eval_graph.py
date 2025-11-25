# eval_graph.py
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Dict, List, Optional

from typing_extensions import TypedDict
from langgraph.graph import StateGraph, END

from impact_evidence_faithfulness import (
    EvidenceItem,
    PerURLImpactEval,
    ImpactEvidenceState,
    evaluate_impact_node,
)
from policy_attribution_consistency import (
    PerURLPolicyAttributionEval,
    PolicyAttributionState,
    evaluate_policy_attribution_node,
)
from eval_tools import URLScraper


class CombinedEvalState(TypedDict, total=False):
    """
    Shared state for running both:
    - Impact Evidence Faithfulness
    - Policy Attribution Consistency
    on a single influence chain segment.
    """

    # Shared high-level context
    politician: Optional[str]
    policy: Optional[str]
    question: Optional[str]

    # Core chain metadata
    industry_or_sector: str
    companies: List[str]
    impact_description: str

    # Evidence list from influence report
    evidence: List[EvidenceItem]

    # Scraped pages (shared by both metrics)
    scraped_pages: List[Dict[str, Any]]

    # Metric-specific outputs
    impact_results: List[PerURLImpactEval]
    attribution_results: List[PerURLPolicyAttributionEval]

    # Combined summary/aggregation
    combined_summary: Dict[str, Any]


async def scrape_urls_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Open all evidence URLs with Playwright and extract page text.

    Input:
        state["evidence"]: list of { "source_title": str, "url": str }

    Output:
        state["scraped_pages"]: list of {
            "url", "ok", "status", "final_url", "title", "text", "error"
        }
    """
    evidence = state.get("evidence", [])
    urls = [ev["url"] for ev in evidence]

    scraper = URLScraper(
        headless=True,
        timeout_ms=20_000,
        wait_until="networkidle",
        max_chars=50_000,
    )

    if not urls:
        return {**state, "scraped_pages": []}

    results = await scraper.fetch_many(urls, concurrency=3)

    return {
        **state,
        "scraped_pages": results,
    }


def _aggregate_impact(impact_results: List[PerURLImpactEval]) -> Dict[str, Any]:
    """
    Aggregate impact evidence scores and labels.
    """
    if not impact_results:
        return {
            "avg_score": None,
            "label_counts": {},
        }

    scores = [r.score for r in impact_results]
    avg_score = sum(scores) / len(scores) if scores else None

    label_counts: Dict[str, int] = defaultdict(int)
    for r in impact_results:
        label_counts[r.label] += 1

    return {
        "avg_score": avg_score,
        "label_counts": dict(label_counts),
    }


def _aggregate_attribution(
    attribution_results: List[PerURLPolicyAttributionEval],
) -> Dict[str, Any]:
    """
    Aggregate policy attribution scores and relation labels.
    """
    if not attribution_results:
        return {
            "avg_score": None,
            "label_counts": {},
            "politician_mentioned_ratio": None,
            "policy_topic_mentioned_ratio": None,
        }

    scores = [r.score for r in attribution_results]
    avg_score = sum(scores) / len(scores) if scores else None

    label_counts: Dict[str, int] = defaultdict(int)
    politician_mentioned = 0
    policy_topic_mentioned = 0

    for r in attribution_results:
        label_counts[r.label] += 1
        if r.politician_mentioned:
            politician_mentioned += 1
        if r.policy_topic_mentioned:
            policy_topic_mentioned += 1

    total = len(attribution_results)
    return {
        "avg_score": avg_score,
        "label_counts": dict(label_counts),
        "politician_mentioned_ratio": politician_mentioned / total if total else None,
        "policy_topic_mentioned_ratio": policy_topic_mentioned / total if total else None,
    }


async def combine_node(state: CombinedEvalState) -> CombinedEvalState:
    """
    Combine impact_evidence and policy_attribution results
    into a single summary object.
    """
    impact_results: List[PerURLImpactEval] = state.get("impact_results", []) or []
    attribution_results: List[PerURLPolicyAttributionEval] = (
        state.get("attribution_results", []) or []
    )

    impact_summary = _aggregate_impact(impact_results)
    attribution_summary = _aggregate_attribution(attribution_results)

    combined_summary: Dict[str, Any] = {
        "impact": impact_summary,
        "attribution": attribution_summary,
        # You can add high-level flags here if you want
        "flags": {
            "has_low_impact_evidence": (
                impact_summary["avg_score"] is not None
                and impact_summary["avg_score"] < 0.5
            ),
            "has_weak_attribution": (
                attribution_summary["avg_score"] is not None
                and attribution_summary["avg_score"] < 0.5
            ),
        },
    }

    return {
        **state,
        "combined_summary": combined_summary,
    }


# ===== Build combined LangGraph workflow =====

workflow = StateGraph(CombinedEvalState)

# 1) Shared URL scraping
workflow.add_node("scrape_urls", scrape_urls_node)

# 2) Impact Evidence Faithfulness (reusing node from impact_evidence_faithfulness.py)
workflow.add_node("evaluate_impact", evaluate_impact_node)

# 3) Policy Attribution Consistency (reusing node from policy_attribution_consistency.py)
workflow.add_node("evaluate_policy_attribution", evaluate_policy_attribution_node)

# 4) Combine node
workflow.add_node("combine", combine_node)

# Graph wiring:
# scrape_urls -> (evaluate_impact, evaluate_policy_attribution)
# both -> combine -> END
workflow.set_entry_point("scrape_urls")
workflow.add_edge("scrape_urls", "evaluate_impact")
workflow.add_edge("scrape_urls", "evaluate_policy_attribution")
workflow.add_edge("evaluate_impact", "combine")
workflow.add_edge("evaluate_policy_attribution", "combine")
workflow.add_edge("combine", END)

combined_eval_app = workflow.compile()
