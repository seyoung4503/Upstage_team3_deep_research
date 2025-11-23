import os
import re
from datetime import datetime
from typing import Annotated, Dict, List, Literal, Optional, TypedDict

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langchain_core.messages import HumanMessage
from langchain_core.tools import InjectedToolArg, tool
from langchain_upstage import ChatUpstage
from playwright.sync_api import sync_playwright
from pydantic import BaseModel
from tavily import TavilyClient

from deep_research.state_research import Summary
from deep_research.prompts import (
    summarize_webpage_prompt,
    naver_queryset_prompt,
)

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
UPSTAGE_API_KEY = os.getenv("UPSTAGE_API_KEY")

# -------------------------------------------------------------------
# Global clients / models
# -------------------------------------------------------------------

google_client: Optional[genai.Client] = None
if GOOGLE_API_KEY:
    google_client = genai.Client(api_key=GOOGLE_API_KEY)

# Upstage models
summarization_model = ChatUpstage(
    api_key=UPSTAGE_API_KEY,
    model="solar-pro2",
    temperature=0.0,
)
# Reuse structured-output model for all summaries
structured_summary_model = summarization_model.with_structured_output(Summary)

query_refiner_model = ChatUpstage(
    api_key=UPSTAGE_API_KEY,
    model="solar-pro2",
    temperature=0.0,
)

tavily_client = TavilyClient()


# -------------------------------------------------------------------
# Common utilities / types
# -------------------------------------------------------------------

class SearchDoc(TypedDict):
    title: str
    content: str  # raw text or summarized text


SearchResultMap = Dict[str, SearchDoc]


def get_today_str() -> str:
    """Return current date as a human-readable string."""
    return datetime.now().strftime("%a %b %-d, %Y")


def summarize_text_block(text: str, max_chars: int = 15000) -> str:
    """
    Summarize a single long text block using the global summarization model.

    Returns formatted summary with <summary> and <key_excerpts> tags.
    """
    truncated = text[:max_chars]
    try:
        summary_obj = structured_summary_model.invoke(
            [
                HumanMessage(
                    content=summarize_webpage_prompt.format(
                        webpage_content=truncated,
                        date=get_today_str(),
                    )
                )
            ]
        )
        return (
            f"<summary>\n{summary_obj.summary}\n</summary>\n\n"
            f"<key_excerpts>\n{summary_obj.key_excerpts}\n</key_excerpts>"
        )
    except Exception as e:  # noqa: BLE001
        print(f"[Summarization error] {e}")
        return text[:2000] + "..."


def summarize_results_map(raw_results: SearchResultMap) -> SearchResultMap:
    """
    Take a url -> {title, content(raw)} map and return a new map
    where 'content' is replaced with a structured summary.
    """
    summarized: SearchResultMap = {}
    for url, doc in raw_results.items():
        summarized[url] = {
            "title": doc["title"],
            "content": summarize_text_block(doc["content"]),
        }
    return summarized


def format_search_results(
    summarized_results: SearchResultMap,
    header: Optional[str] = None,
) -> str:
    """
    Format summarized search results into a unified, human-readable string.

    This is shared by both Tavily and Naver search tools.
    """
    if not summarized_results:
        return (
            "No valid search results found. "
            "Please try different search queries or use a different search API."
        )

    lines: List[str] = []
    if header:
        lines.append(header.rstrip())
        lines.append("")

    lines.append("Search results:")

    for i, (url, result) in enumerate(summarized_results.items(), start=1):
        lines.append(f"\n\n--- SOURCE {i}: {result['title']} ---")
        lines.append(f"URL: {url}\n")
        lines.append("SUMMARY:")
        lines.append(result["content"])
        lines.append("\n" + "-" * 80)

    return "\n".join(lines)


# -------------------------------------------------------------------
# Tavily backend + tool
# -------------------------------------------------------------------

def tavily_backend(
    query: str,
    max_results: int = 3,
    topic: Literal["general", "news", "finance"] = "general",
) -> SearchResultMap:
    """
    Call Tavily API and return url -> {title, content(raw)} map.
    """
    api_result = tavily_client.search(
        query,
        max_results=max_results,
        include_raw_content=True,
        topic=topic,
    )

    raw_map: SearchResultMap = {}
    for item in api_result.get("results", []):
        url = item.get("url")
        if not url or url in raw_map:
            continue

        content = (
            item.get("raw_content")
            or item.get("content")
            or ""
        )
        raw_map[url] = {
            "title": item.get("title", ""),
            "content": content,
        }

    return raw_map


@tool(parse_docstring=True)
def tavily_search(
    query: str,
    max_results: Annotated[int, InjectedToolArg] = 3,
    topic: Annotated[Literal["general", "news", "finance"], InjectedToolArg] = "general",
) -> str:
    """
    Fetch results from Tavily search API, summarize each document,
    and return a formatted string of all results.

    Args:
        query: A single search query to execute.
        max_results: Maximum number of results to return.
        topic: Topic to filter results by ('general', 'news', 'finance').

    Returns:
        A formatted string of search results with summaries.
    """
    raw = tavily_backend(query=query, max_results=max_results, topic=topic)
    summarized = summarize_results_map(raw)
    return format_search_results(summarized)


# -------------------------------------------------------------------
# Think tool
# -------------------------------------------------------------------

@tool(parse_docstring=True)
def think_tool(reflection: str) -> str:
    """
    Tool for strategic reflection on research progress and decision-making.

    Use this tool after each search to analyze results and plan next steps.
    This creates a deliberate pause in the research workflow for
    higher-quality decision-making.

    Args:
        reflection: Detailed reflection on research progress, findings,
                    gaps, and next steps.

    Returns:
        A short confirmation string.
    """
    return f"Reflection recorded: {reflection}"


# -------------------------------------------------------------------
# Naver scraping utilities
# -------------------------------------------------------------------

def fetch_clean_content(url: str) -> str:
    """
    Fetch and extract the main body text from a URL using Playwright + BeautifulSoup.

    - Handles Naver news, blog, cafe, and general web pages.
    - If the page is too short or blocked by login, returns a short message instead.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            try:
                page.goto(url, timeout=5000, wait_until="domcontentloaded")
            except Exception:  # noqa: BLE001
                # Ignore navigation timeout; we still attempt to read content.
                pass

            try:
                page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:  # noqa: BLE001
                pass

            target_frame = page
            if "blog.naver.com" in url:
                frame = page.frame(name="mainFrame")
                if frame:
                    target_frame = frame

            html = target_frame.content()
            browser.close()

        soup = BeautifulSoup(html, "html.parser")

        # Remove non-content elements
        for tag in soup(
            ["script", "style", "header", "footer", "nav", "aside", "form", "iframe"]
        ):
            tag.decompose()

        # Heuristic main-content selectors
        if "blog.naver.com" in url:
            main_content = (
                soup.select_one(".se-main-container")
                or soup.select_one("#postViewArea")
            )
        elif "n.news.naver.com" in url:
            main_content = (
                soup.select_one("#dic_area")
                or soup.select_one("#articleBodyContents")
            )
        elif "cafe.naver.com" in url:
            main_content = soup.select_one(".gate_box")
        else:
            main_content = soup.body

        target_soup = main_content if main_content else soup
        clean_text = target_soup.get_text(separator=" ", strip=True)
        clean_text = re.sub(r"\s+", " ", clean_text)

        if "로그인" in clean_text and "해주세요" in clean_text:
            return "🔒 [접근 제한] 로그인 필요한 페이지입니다."

        return clean_text[:4000]

    except Exception as e:  # noqa: BLE001
        return f"❌ 스크래핑 오류: {e}"


def deep_search_naver_internal(
    refined_query: str,
    needs_recency: bool,
    max_results: int = 5,
) -> SearchResultMap:
    """
    Search Naver OpenAPI (news/webkr/blog), scrape each page,
    and return url -> {title, content(raw)} map.
    """
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return {}

    sections = ["news", "webkr", "blog"]
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    sort_opt = "date" if needs_recency else "sim"

    results_by_url: SearchResultMap = {}
    per_section = max(1, max_results // len(sections))

    for section in sections:
        api_url = f"https://openapi.naver.com/v1/search/{section}.json"
        params = {
            "query": refined_query,
            "display": per_section,
            "start": 1,
            "sort": sort_opt,
        }

        try:
            resp = requests.get(api_url, headers=headers, params=params, timeout=5)
            data = resp.json()
            items = data.get("items", [])

            for item in items:
                raw_title = item.get("title", "")
                # Strip HTML tags in title
                title = re.sub("<[^<]+?>", "", raw_title)
                link = item.get("link", "")

                if not link or link in results_by_url:
                    continue

                full_text = fetch_clean_content(link)

                # Fallback to API description if page is not accessible or too short
                if len(full_text) < 50 or "로그인" in full_text:
                    desc = item.get("description", "")
                    full_text = f"(요약) {re.sub('<[^<]+?>', '', desc)}"

                results_by_url[link] = {
                    "title": title,
                    "content": full_text,
                }

                if len(results_by_url) >= max_results:
                    break

            if len(results_by_url) >= max_results:
                break

        except Exception as e:  # noqa: BLE001
            print(f"[Naver API Error] section={section} err={e}")
            continue

    return results_by_url


# -------------------------------------------------------------------
# Naver query set generation (LLM-based)
# -------------------------------------------------------------------

class KRQuerySet(BaseModel):
    """
    Structured representation of Naver-style queries inferred from a Korean question.
    """
    intent_type: Literal["manga", "person", "cafe", "weather", "event", "generic"]
    main_entity: str
    queries: List[str]


def detect_recency_by_keyword(text: str) -> bool:
    """
    Heuristic recency detector based on Korean temporal keywords.
    Used in addition to LLM reasoning.
    """
    recent_keywords = ["어제", "오늘", "최근", "현재", "올해", "지금", "실시간", "강수량"]
    if any(k in text for k in recent_keywords):
        return True

    # Roughly match years like 2000~2039.
    if re.search(r"20[0-3][0-9]년", text):
        return True

    return False


def generate_naver_style_queries(original_question: str) -> KRQuerySet:
    """
    Convert a Korean natural-language question into multiple short Naver-style
    search queries using an LLM.

    The prompt content is fully defined in `naver_queryset_prompt` and should
    be written in Korean, with {today} and {question} placeholders.
    """
    today = get_today_str()
    prompt = naver_queryset_prompt.format(
        today=today,
        question=original_question,
    )

    structured = query_refiner_model.with_structured_output(KRQuerySet)
    result: KRQuerySet = structured.invoke(
        [HumanMessage(content=prompt)]
    )
    return result


# -------------------------------------------------------------------
# Naver backend + tool
# -------------------------------------------------------------------

def naver_backend(
    question: str,
    max_results: int = 5,
) -> SearchResultMap:
    """
    High-level Naver backend:
    - Use LLM to generate multiple Naver-style keyword queries.
    - For each query, call Naver OpenAPI + scraping to get raw documents.
    - Merge into a url -> {title, content(raw)} map.
    """
    qset = generate_naver_style_queries(question)

    print(f"[NAVER] original: {question}")
    print(f"[NAVER] intent  : {qset.intent_type}, main_entity={qset.main_entity}")
    print(f"[NAVER] queries : {qset.queries}")

    all_results: SearchResultMap = {}
    per_query_limit = max(1, max_results // max(len(qset.queries), 1))

    for sub_q in qset.queries:
        raw_by_url = deep_search_naver_internal(
            refined_query=sub_q,
            needs_recency=detect_recency_by_keyword(sub_q),
            max_results=per_query_limit,
        )
        for url, doc in raw_by_url.items():
            if url not in all_results:
                all_results[url] = {
                    "title": doc["title"],
                    "content": doc["content"],
                }

        if len(all_results) >= max_results:
            break

    return all_results


@tool(parse_docstring=True)
def naver_search(
    question: str,
    max_results: Annotated[int, "Maximum number of sources to use"] = 5,
) -> str:
    """
    Korean-focused search tool that uses:
    - Naver OpenAPI (news/webkr/blog) + Playwright scraping
    - LLM-based query generation for Naver-style keywords
    - The same summarization pipeline as Tavily search

    You should pass the user's original Korean question (natural language).
    The tool internally converts it into multiple short keyword queries and
    returns a formatted string compatible with Tavily search results.
    
    Args:
        question: Original Korean question from the user.
        max_results: Maximum number of unique URLs to process.

    Returns:
        A formatted string that starts with "[NAVER_SEARCH]" and contains
        the inferred intent, main entity, generated keyword queries,
        and summarized content for each source.
    """
    raw = naver_backend(question=question, max_results=max_results)
    summarized = summarize_results_map(raw)

    header = (
        "[NAVER_SEARCH]\n"
        f"question  : {question}\n"
    )
    return format_search_results(summarized, header=header)


# -------------------------------------------------------------------
# Google Search Grounded backend + tool
# -------------------------------------------------------------------

def google_grounded_backend(
    query: str,
    model: str = "gemini-2.5-flash",
) -> str:
    """
    Call Gemini with Google Search grounding and return an answer + sources.

    This helper:
    - uses the Google Search tool (Search Grounding)
    - lets Gemini decide which web queries to run
    - returns the model's answer text plus a simple "Sources" list if available
    """
    if google_client is None:
        return (
            "[GOOGLE_SEARCH]\n"
            "Google Search grounding is not configured "
            "(missing GOOGLE_API_KEY in environment).\n"
        )

    grounding_tool = types.Tool(
        google_search=types.GoogleSearch()
    )
    config = types.GenerateContentConfig(tools=[grounding_tool])

    response = google_client.models.generate_content(
        model=model,
        contents=query,
        config=config,
    )

    text = response.text or ""
    sources_lines: List[str] = []

    try:
        candidate = response.candidates[0]
        meta = getattr(candidate, "grounding_metadata", None)
        if meta and getattr(meta, "grounding_chunks", None):
            chunks = meta.grounding_chunks
            for i, ch in enumerate(chunks, start=1):
                web = getattr(ch, "web", None)
                if not web:
                    continue
                uri = getattr(web, "uri", None)
                title = getattr(web, "title", "") or ""
                if uri:
                    sources_lines.append(f"[{i}] {title}: {uri}")
    except Exception as e:  # noqa: BLE001
        print(f"[Google Search grounding] metadata parse error: {e}")

    output_parts: List[str] = []
    output_parts.append("[GOOGLE_SEARCH]")
    output_parts.append(f"model: {model} (grounded by Google Search)\n")
    output_parts.append("ANSWER:\n")
    output_parts.append(text.strip())

    if sources_lines:
        output_parts.append("\n\nSources:")
        output_parts.append("\n".join(sources_lines))

    return "\n".join(output_parts)


@tool(parse_docstring=True)
def google_search_grounded(
    question: str,
) -> str:
    """
    Use Gemini with Google Search grounding to answer a question.

    This tool:
    - Sends the user's question to a Gemini model with the Google Search tool enabled.
    - Lets the model decide which web searches to run.
    - Returns a grounded answer text plus a list of web sources, if available.

    Args:
        question: The user's natural language question.

    Returns:
        A string that starts with "[GOOGLE_SEARCH]" and contains:
        - the grounded answer text, and
        - a "Sources" section listing the URLs used as evidence.
    """
    return google_grounded_backend(question)
