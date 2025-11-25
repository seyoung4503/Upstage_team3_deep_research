import os
import asyncio
from typing import Any, Dict, List, Literal, Optional

from typing_extensions import TypedDict
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from eval_prompt import policy_attribution_prompt

from eval_tools import URLScraper

load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")


llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    api_key=API_KEY,
    temperature=0,
    convert_system_message_to_human=True,
)



RelationLabel = Literal[
    "HIGHLY_RELATED",
    "WEAKLY_RELATED",  
    "UNRELATED",  
    "NOT_ENOUGH_INFO", 
]

ErrorType = Literal[
    "NONE",
    "PAGE_LOAD_ERROR",
    "LOGIN_REQUIRED",
    "REDIRECTED_TO_HOME",
    "TOO_SHORT",
    "OTHER",
]


class PolicyAttributionResult(BaseModel):
    """LLM-structured output for policy attribution consistency of a single page."""

    label: RelationLabel = Field(
        ...,
        description="How strongly this page is related to (politician, policy).",
    )
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0.",
    )
    reasoning: str = Field(
        ...,
        description="Short explanation in Korean of how the article relates to the politician and policy.",
    )
    evidence_spans: List[str] = Field(
        default_factory=list,
        description="Short spans from source_text that show politician/policy relevance.",
    )
    error_type: ErrorType = Field(
        "NONE",
        description="Page loading or content error type.",
    )

    politician_mentioned: bool = Field(
        ...,
        description="True if the politician appears in the article in a meaningful way.",
    )
    policy_topic_mentioned: bool = Field(
        ...,
        description="True if the specific policy or its core topic appears meaningfully.",
    )


class PerURLPolicyAttributionEval(BaseModel):
    """Evaluation result for a single evidence URL."""

    url: str
    source_title: Optional[str] = None
    http_status: Optional[int] = None
    ok: bool = False

    label: RelationLabel
    score: float
    reasoning: str
    evidence_spans: List[str]
    error_type: ErrorType

    politician_mentioned: bool
    policy_topic_mentioned: bool


class EvidenceItem(TypedDict):
    source_title: str
    url: str


class PolicyAttributionState(TypedDict, total=False):
    """
    State for evaluating policy attribution consistency
    of a single influence chain with multiple evidence URLs.
    """

    # Core metadata for this chain
    politician: str
    policy: str
    industry_or_sector: str
    companies: List[str]
    question: Optional[str]

    # Evidence list from influence report
    evidence: List[EvidenceItem]

    # Scraped pages
    scraped_pages: List[Dict[str, Any]]

    # LLM evaluation outputs
    attribution_results: List[PerURLPolicyAttributionEval]


prompt = ChatPromptTemplate.from_template(policy_attribution_prompt)
structured_llm = llm.with_structured_output(PolicyAttributionResult,strict=True)
policy_chain = prompt | structured_llm


async def _evaluate_single_url(
    chain,
    politician: str,
    policy: str,
    industry_or_sector: str,
    companies: List[str],
    source_title: str,
    url: str,
    source_text: str,
    question: str,
    http_status: Optional[int],
    ok: bool,
) -> PerURLPolicyAttributionEval:
    """
    Run policy attribution consistency prompt + structured output for a single URL.
    """
    companies_str = ", ".join(companies) if companies else ""

    result: PolicyAttributionResult = await chain.ainvoke(
        {
            "politician": politician,
            "policy": policy,
            "industry_or_sector": industry_or_sector,
            "companies": companies_str,
            "source_title": source_title,
            "url": url,
            "source_text": source_text,
            "question": question,
        }
    )

    return PerURLPolicyAttributionEval(
        url=url,
        source_title=source_title or None,
        http_status=http_status,
        ok=ok,
        label=result.label,
        score=result.score,
        reasoning=result.reasoning,
        evidence_spans=result.evidence_spans,
        error_type=result.error_type,
        politician_mentioned=result.politician_mentioned,
        policy_topic_mentioned=result.policy_topic_mentioned,
    )


async def evaluate_policy_attribution_node(
    state: PolicyAttributionState,
) -> PolicyAttributionState:
    """
    For each evidence URL and its scraped text, call the LLM judge
    and compute policy attribution consistency for this influence chain.
    """

    evidence = state.get("evidence", [])
    scraped_pages = state.get("scraped_pages", [])

    politician = state["politician"]
    policy = state["policy"]
    industry_or_sector = state["industry_or_sector"]
    companies = state["companies"]
    question = state.get("question", "") or ""

    results: List[PerURLPolicyAttributionEval] = []
    tasks = []

    for ev, page in zip(evidence, scraped_pages):
        source_title = ev.get("source_title") or page.get("title") or ""
        url = ev["url"]
        source_text = page.get("text", "") or ""
        http_status = page.get("status")
        ok = bool(page.get("ok"))

        tasks.append(
            _evaluate_single_url(
                chain=policy_chain,
                politician=politician,
                policy=policy,
                industry_or_sector=industry_or_sector,
                companies=companies,
                source_title=source_title,
                url=url,
                source_text=source_text,
                question=question,
                http_status=http_status,
                ok=ok,
            )
        )

    if tasks:
        per_url_results = await asyncio.gather(*tasks)
        results.extend(per_url_results)

    return {
        "attribution_results": results,
    }
