import os
import asyncio
from typing import Any, Dict, List, Literal, Optional

from typing_extensions import TypedDict
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END

from eval_tools import URLScraper
from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv
from eval_prompt import impact_citation


load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    api_key=API_KEY,
    temperature=0.0,
    convert_system_message_to_human=True,
)


LabelType = Literal[
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "UNSUPPORTED",
    "CONTRADICTED",
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


class ImpactEvidenceResult(BaseModel):
    """Structured output for impact evidence faithfulness on a single page."""

    label: LabelType = Field(..., description="Impact evidence faithfulness label")
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0",
    )
    reasoning: str = Field(
        ...,
        description="Short explanation of how the impact description and source text were compared",
    )
    evidence_spans: List[str] = Field(
        default_factory=list,
        description="Short spans from source_text that support or contradict the impact description",
    )
    error_type: ErrorType = Field(
        "NONE",
        description="Page loading or content error type",
    )


class PerURLImpactEval(BaseModel):
    """Evaluation result for a single evidence URL."""

    url: str
    source_title: Optional[str] = None
    http_status: Optional[int] = None
    ok: bool = False

    label: LabelType
    score: float
    reasoning: str
    evidence_spans: List[str]
    error_type: ErrorType


class EvidenceItem(TypedDict):
    source_title: str
    url: str



class ImpactEvidenceState(TypedDict, total=False):
    """
    State for evaluating impact evidence faithfulness of a single
    influence chain segment with multiple evidence URLs.
    """

    # Optional high-level context
    politician: Optional[str]
    policy: Optional[str]
    question: Optional[str]

    # Core impact metadata
    industry_or_sector: str
    companies: List[str]
    impact_description: str

    # Evidence list from influence report
    evidence: List[EvidenceItem]

    # Scraped pages (URLScraper results)
    scraped_pages: List[Dict[str, Any]]

    # LLM evaluation output (per URL)
    impact_results: List[PerURLImpactEval]


impact_evidence_prompt = ChatPromptTemplate.from_template(impact_citation)

structured_llm = llm.with_structured_output(ImpactEvidenceResult, strict=True)
impact_chain = impact_evidence_prompt | structured_llm


async def _evaluate_single_url(
    chain,
    industry_or_sector: str,
    companies: List[str],
    impact_description: str,
    source_title: str,
    url: str,
    source_text: str,
    question: str,
    http_status: Optional[int],
    ok: bool,
) -> PerURLImpactEval:
    """
    Run impact_evidence faithfulness prompt + structured output for a single URL.
    """
    companies_str = ", ".join(companies) if companies else ""

    result: ImpactEvidenceResult = await chain.ainvoke(
        {
            "industry_or_sector": industry_or_sector,
            "companies": companies_str,
            "impact_description": impact_description,
            "source_title": source_title,
            "url": url,
            "source_text": source_text,
            "question": question,
        }
    )

    return PerURLImpactEval(
        url=url,
        source_title=source_title or None,
        http_status=http_status,
        ok=ok,
        label=result.label,
        score=result.score,
        reasoning=result.reasoning,
        evidence_spans=result.evidence_spans,
        error_type=result.error_type,
    )


async def evaluate_impact_node(state: ImpactEvidenceState) -> ImpactEvidenceState:
    """
    For each evidence URL and its scraped text, call the LLM judge
    and compute impact evidence faithfulness for the given influence chain.
    """

    evidence = state.get("evidence", [])
    scraped_pages = state.get("scraped_pages", [])

    industry_or_sector = state["industry_or_sector"]
    companies = state["companies"]
    impact_description = state["impact_description"]
    question = state.get("question", "") or ""

    results: List[PerURLImpactEval] = []
    tasks = []

    # Evidence and scraped_pages are assumed to have the same order
    for ev, page in zip(evidence, scraped_pages):
        source_title = ev.get("source_title") or page.get("title") or ""
        url = ev["url"]
        source_text = page.get("text", "") or ""
        http_status = page.get("status")
        ok = bool(page.get("ok"))

        tasks.append(
            _evaluate_single_url(
                chain=impact_chain,
                industry_or_sector=industry_or_sector,
                companies=companies,
                impact_description=impact_description,
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
        "impact_results": results,
    }

