import os
from typing import Any, Dict, List, Optional

from typing_extensions import TypedDict
from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, END
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv

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
from eval_prompt import gold_compare


class CombinedEvalState(TypedDict, total=False):
    """
    Shared state used to run:
    - Impact Evidence Faithfulness
    - Policy Attribution Consistency
    - Gold vs Model report comparison
    for a single influence chain or report.
    """

    # High-level context
    politician: Optional[str]
    policy: Optional[str]
    question: Optional[str]

    # Chain-level metadata (for per-chain evaluation)
    industry_or_sector: str
    companies: List[str]
    impact_description: str

    # Evidence list from the model's influence report
    evidence: List[EvidenceItem]

    # Scraped pages for all evidence URLs
    scraped_pages: List[Dict[str, Any]]

    # Metric-specific outputs
    impact_results: List[PerURLImpactEval]
    attribution_results: List[PerURLPolicyAttributionEval]

    # Gold vs model report comparison
    gold_report: Optional[Dict[str, Any]]
    model_report: Optional[Dict[str, Any]]
    gold_eval: Optional[Dict[str, Any]]

    combined_summary: Dict[str, Any]


async def scrape_urls_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetch all evidence URLs using Playwright (via URLScraper)
    and attach the scraped page info to the state as `scraped_pages`.
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


# =========================
# Gold vs Model Report Comparison
# =========================

load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")

gold_llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    api_key=API_KEY,
    temperature=0.0,
    convert_system_message_to_human=True,
)



class GoldCompareResult(BaseModel):
    """Structured output for comparing gold_report and model_report."""

    similarity_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Similarity score between 0.0 and 1.0.",
    )
    reasoning: str = Field(
        ...,
        description="Short Korean explanation of how the two reports were compared.",
    )
    model_unique_points: List[str] = Field(
        default_factory=list,
        description="Key points that appear only in the model_report.",
    )
    gold_unique_points: List[str] = Field(
        default_factory=list,
        description="Key points that appear only in the gold_report.",
    )


gold_prompt = ChatPromptTemplate.from_template(gold_compare)
gold_structured_llm = gold_llm.with_structured_output(GoldCompareResult, strict=True)
gold_chain = gold_prompt | gold_structured_llm


async def evaluate_gold_node(state: CombinedEvalState) -> CombinedEvalState:
    """
    Compare `gold_report` and `model_report` using an LLM-as-judge.

    This node expects:
    - state["question"]
    - state["gold_report"]
    - state["model_report"]

    If either gold_report or model_report is missing, this node
    simply returns the state unchanged.
    """
    question = state.get("question", "") or ""
    gold_report = state.get("gold_report")
    model_report = state.get("model_report")

    if gold_report is None or model_report is None:
        # Nothing to compare; skip this node
        return state

    result: GoldCompareResult = await gold_chain.ainvoke(
        {
            "question": question,
            "gold_report": gold_report,
            "model_report": model_report,
        }
    )

    return {
        **state,
        "gold_eval": result.model_dump(),
    }


async def combine_node(state: CombinedEvalState) -> CombinedEvalState:
    """
    Merge the outputs from:
    - impact_evidence_faithfulness
    - policy_attribution_consistency
    - gold vs model report comparison

    into a single `combined_summary` object.
    No extra aggregation or scoring is done here; we simply
    package the raw results so that a downstream script can
    perform any desired analysis.
    """
    combined_summary: Dict[str, Any] = {
        "impact_results": state.get("impact_results", []),
        "attribution_results": state.get("attribution_results", []),
        "gold_eval": state.get("gold_eval"),
    }

    return {
        **state,
        "combined_summary": combined_summary,
    }



workflow = StateGraph(CombinedEvalState)
workflow.add_node("scrape_urls", scrape_urls_node)
workflow.add_node("evaluate_impact", evaluate_impact_node)
workflow.add_node("evaluate_policy_attribution", evaluate_policy_attribution_node)
workflow.add_node("evaluate_gold", evaluate_gold_node)

workflow.add_node("combine", combine_node)

workflow.set_entry_point("scrape_urls")
workflow.add_edge("scrape_urls", "evaluate_impact")
workflow.add_edge("scrape_urls", "evaluate_policy_attribution")
workflow.add_edge("scrape_urls", "evaluate_gold")

workflow.add_edge("evaluate_impact", "combine")
workflow.add_edge("evaluate_policy_attribution", "combine")
workflow.add_edge("evaluate_gold", "combine")

workflow.add_edge("combine", END)

combined_eval_app = workflow.compile()
