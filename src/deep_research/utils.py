
"""Research Utilities and Tools.

This module provides search and content processing utilities for the research agent,
including web search capabilities and content summarization tools.
"""

from pathlib import Path
from datetime import datetime
from typing_extensions import Annotated, List, Literal
import os
import re

from langchain_upstage import ChatUpstage
from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool, InjectedToolArg
from tavily import TavilyClient

from deep_research.state_research import Summary
from deep_research.prompts import summarize_webpage_prompt


from pydantic import BaseModel
from typing import List, Literal, Annotated, Optional

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

import requests
from typing import Annotated, Optional, Dict



# ===== UTILITY FUNCTIONS =====

def get_today_str() -> str:
    """Get current date in a human-readable format."""
    return datetime.now().strftime("%a %b %-d, %Y")

def get_current_dir() -> Path:
    """Get the current directory of the module.

    This function is compatible with Jupyter notebooks and regular Python scripts.

    Returns:
        Path object representing the current directory
    """
    try:
        return Path(__file__).resolve().parent
    except NameError:  # __file__ is not defined
        return Path.cwd()

# ===== CONFIGURATION =====

# summarization_model = ChatGoogleGenerativeAI(
#     model="gemini-2.5-flash", 
#     api_key = API_KEY,
#     temperature=0,
#     convert_system_message_to_human=True 
# )

summarization_model = ChatUpstage(api_key=os.getenv("UPSTAGE_API_KEY"), model="solar-pro2", temperature=0)

tavily_client = TavilyClient()

# ===== SEARCH FUNCTIONS =====

def tavily_search_multiple(
    search_queries: List[str], 
    max_results: int = 3, 
    topic: Literal["general", "news", "finance"] = "general", 
    include_raw_content: bool = True, 
) -> List[dict]:
    """Perform search using Tavily API for multiple queries.

    Args:
        search_queries: List of search queries to execute
        max_results: Maximum number of results per query
        topic: Topic filter for search results
        include_raw_content: Whether to include raw webpage content

    Returns:
        List of search result dictionaries
    """

    # Execute searches sequentially. Note: yon can use AsyncTavilyClient to parallelize this step.
    search_docs = []
    for query in search_queries:
        result = tavily_client.search(
            query,
            max_results=max_results,
            include_raw_content=include_raw_content,
            topic=topic
        )
        search_docs.append(result)

    return search_docs

# todo : 성능 테스트 필요
def summarize_webpage_content(webpage_content: str) -> str: 
    """Summarize webpage content using the configured summarization model.

    Args:
        webpage_content: Raw webpage content to summarize

    Returns:
        Formatted summary with key excerpts
    """
    MAX_CHARS = 15000  
    try:
        truncated = webpage_content[:MAX_CHARS]

        structured_model = summarization_model.with_structured_output(Summary)
        summary = structured_model.invoke([
            HumanMessage(content=summarize_webpage_prompt.format(
                webpage_content=truncated,
                date=get_today_str()
            ))
        ])
        formatted_summary = (
            f"<summary>\n{summary.summary}\n</summary>\n\n"
            f"<key_excerpts>\n{summary.key_excerpts}\n</key_excerpts>"
        )
        return formatted_summary
    except Exception as e:
        print(f"Failed to summarize webpage: {str(e)}")
        return webpage_content[:2000] + "..."
    # try:
    #     # Set up structured output model for summarization
    #     structured_model = summarization_model.with_structured_output(Summary)

    #     # Generate summary
    #     summary = structured_model.invoke([
    #         HumanMessage(content=summarize_webpage_prompt.format(
    #             webpage_content=webpage_content, 
    #             date=get_today_str()
    #         ))
    #     ])

    #     # Format summary with clear structure
    #     formatted_summary = (
    #         f"<summary>\n{summary.summary}\n</summary>\n\n"
    #         f"<key_excerpts>\n{summary.key_excerpts}\n</key_excerpts>"
    #     )

    #     return formatted_summary

    # except Exception as e:
    #     print(f"Failed to summarize webpage: {str(e)}")
    #     return webpage_content[:1000] + "..." if len(webpage_content) > 1000 else webpage_content

def deduplicate_search_results(search_results: List[dict]) -> dict:
    """Deduplicate search results by URL to avoid processing duplicate content.

    Args:
        search_results: List of search result dictionaries

    Returns:
        Dictionary mapping URLs to unique results
    """
    unique_results = {}

    for response in search_results:
        for result in response['results']:
            url = result['url']
            if url not in unique_results:
                unique_results[url] = result

    return unique_results

def process_search_results(unique_results: dict) -> dict:
    """Process search results by summarizing content where available.

    Args:
        unique_results: Dictionary of unique search results

    Returns:
        Dictionary of processed results with summaries
    """
    summarized_results = {}

    for url, result in unique_results.items():
        # Use existing content if no raw content for summarization
        if not result.get("raw_content"):
            content = result['content']
        else:
            # Summarize raw content for better processing
            content = summarize_webpage_content(result['raw_content'])

        summarized_results[url] = {
            'title': result['title'],
            'content': content
        }

    return summarized_results

def format_search_output(summarized_results: dict) -> str:
    """Format search results into a well-structured string output.

    Args:
        summarized_results: Dictionary of processed search results

    Returns:
        Formatted string of search results with clear source separation
    """
    if not summarized_results:
        return "No valid search results found. Please try different search queries or use a different search API."

    formatted_output = "Search results: \n\n"

    for i, (url, result) in enumerate(summarized_results.items(), 1):
        formatted_output += f"\n\n--- SOURCE {i}: {result['title']} ---\n"
        formatted_output += f"URL: {url}\n\n"
        formatted_output += f"SUMMARY:\n{result['content']}\n\n"
        formatted_output += "-" * 80 + "\n"

    return formatted_output

# ===== RESEARCH TOOLS =====

@tool(parse_docstring=True)
def tavily_search(
    query: str,
    max_results: Annotated[int, InjectedToolArg] = 3,
    topic: Annotated[Literal["general", "news", "finance"], InjectedToolArg] = "general",
) -> str:
    """Fetch results from Tavily search API with content summarization.

    Args:
        query: A single search query to execute
        max_results: Maximum number of results to return
        topic: Topic to filter results by ('general', 'news', 'finance')

    Returns:
        Formatted string of search results with summaries
    """
    # Execute search for single query
    search_results = tavily_search_multiple(
        [query],  # Convert single query to list for the internal function
        max_results=max_results,
        topic=topic,
        include_raw_content=False, # todo : html check
    )

    # Deduplicate results by URL to avoid processing duplicate content
    unique_results = deduplicate_search_results(search_results)

    # Process results with summarization
    summarized_results = process_search_results(unique_results)

    # Format output for consumption
    return format_search_output(summarized_results)

@tool(parse_docstring=True)
def think_tool(reflection: str) -> str:
    """Tool for strategic reflection on research progress and decision-making.

    Use this tool after each search to analyze results and plan next steps systematically.
    This creates a deliberate pause in the research workflow for quality decision-making.

    When to use:
    - After receiving search results: What key information did I find?
    - Before deciding next steps: Do I have enough to answer comprehensively?
    - When assessing research gaps: What specific information am I still missing?
    - Before concluding research: Can I provide a complete answer now?

    Reflection should address:
    1. Analysis of current findings - What concrete information have I gathered?
    2. Gap assessment - What crucial information is still missing?
    3. Quality evaluation - Do I have sufficient evidence/examples for a good answer?
    4. Strategic decision - Should I continue searching or provide my answer?

    Args:
        reflection: Your detailed reflection on research progress, findings, gaps, and next steps

    Returns:
        Confirmation that reflection was recorded for decision-making
    """
    return f"Reflection recorded: {reflection}"




NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# ==========================
# 1) Query Refiner 구조체
# ==========================

class RefinedQuery(BaseModel):
    """Structured output for Naver query refinement."""
    refined_query: str
    # "최신"/"현재"/"어제" 등 time-sensitive 여부
    needs_recency: bool = False
    # 선택: 사이트 힌트 (나무위키, 위키백과 등)
    site_hint: Optional[str] = None


# 네가 쓰던 모델 그대로
query_refiner_model = ChatUpstage(model="solar-pro", temperature=0.0)


def refine_kr_search_query(original_question: str) -> RefinedQuery:
    """
    LLM으로 네이버용 검색 쿼리를 정제하는 **동기** 함수.
    RefinedQuery(pydantic) 형태로 반환된다.
    """
    today = get_today_str()

    system_msg = (
        "You are a Korean search query refiner for Naver (뉴스/웹/블로그) search.\n"
        "Your job is to convert a natural language question into a concise, "
        "search-engine-friendly Korean query.\n"
        "You MUST output a JSON object with fields: refined_query, needs_recency, site_hint."
    )

    user_msg = f"""
    너는 '네이버 검색 엔진'의 작동 원리를 완벽히 이해하는 [검색 쿼리 생성 전문가]야.
    사용자의 질문을 입력받아, 네이버에서 **가장 정확한 검색 결과가 나올 수 있는 '키워드 조합'**으로 변환해.

    [사용자 질문]
    {original_question}

    [기준일]: {today}

    [변환 규칙]
    1. 문장을 해체하라: 조사('은', '는', '이', '가', '을', '를')와 서술어('알려줘', '궁금해')를 모두 제거해.
    2. 행정 용어 금지: '통계', '현황', '수치', '기준' 같은 딱딱한 단어는 블로그/뉴스 제목에 잘 안 쓰이니 되도록 피하고,
       대신 '근황', '최신', '속보', '발표', '몇권', '얼마', '가격' 같은 자연스러운 표현을 써.
    3. 날짜 구체화:
       - 질문에 '어제', '오늘', '현재', '최근', '올해' 등이 나오면 [기준일]을 기준으로
         'YYYY년 M월 D일' 또는 'YYYY년' 같은 구체적인 표현을 포함하는 검색어로 만들어라.
    4. 섹션 타겟팅(힌트 차원):
       - 수치/가격 질문 -> '가격', '얼마'
       - 인물 질문 -> '프로필', '나이', '최근'
       - 만화/책 질문 -> '몇권', '신간', '발매일'
       - 커뮤니티/반응 -> '후기', '반응'

    [출력 형식 - 중요]
    아래 형식의 JSON만 출력해. 다른 문장은 절대 쓰지 마.
    {{
      "refined_query": "<네이버 검색창에 넣을 최종 검색어>",
      "needs_recency": <true 또는 false>,
      "site_hint": "<'나무위키', '위키백과', '공식 사이트', '없음' 중 하나 또는 null>"
    }}

    - refined_query: 네이버 검색창에 그대로 넣으면 좋은 한국어 키워드 조합.
    - needs_recency: '현재/최근/어제/올해/지금' 등 시간이 중요한 질문이면 true, 아니면 false.
    - site_hint: 특정 사이트가 유리하면 간단히 힌트. 없으면 "없음" 또는 null.
    """

    structured = query_refiner_model.with_structured_output(RefinedQuery)
    result: RefinedQuery = structured.invoke(
        [
            HumanMessage(role="system", content=system_msg),
            HumanMessage(role="user", content=user_msg),
        ]
    )
    return result



# ===================================
# 2) Playwright + BeautifulSoup 본문 추출 (sync)
# ===================================

def fetch_clean_content(url: str) -> str:
    """
    URL에 접속하여 '본문 영역'만 최대한 깔끔하게 추출하는 **동기 함수**.
    - 네이버 뉴스, 블로그, 카페, 일반 웹 등을 처리
    - 너무 짧거나, 로그인 막히면 에러 메시지 반환
    """
    clean_text = ""

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
            except Exception:
                # 타임아웃 나더라도 일단 DOM 있는 범위에서 진행
                pass

            try:
                page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                pass

            target_frame = page
            # 네이버 블로그 iframe 처리
            if "blog.naver.com" in url:
                frame = page.frame(name="mainFrame")
                if frame:
                    target_frame = frame

            html = target_frame.content()
            browser.close()

        soup = BeautifulSoup(html, "html.parser")

        # 스크립트/스타일/네비/푸터 제거
        for tag in soup(["script", "style", "header", "footer", "nav", "aside", "form", "iframe"]):
            tag.decompose()

        main_content = None

        if "blog.naver.com" in url:
            main_content = soup.select_one(".se-main-container") or soup.select_one("#postViewArea")
        elif "n.news.naver.com" in url:
            main_content = soup.select_one("#dic_area") or soup.select_one("#articleBodyContents")
        elif "cafe.naver.com" in url:
            main_content = soup.select_one(".gate_box")  # 로그인 막혀도 대문 텍스트 정도
        else:
            main_content = soup.body

        target_soup = main_content if main_content else soup
        clean_text = target_soup.get_text(separator=" ", strip=True)
        clean_text = re.sub(r"\s+", " ", clean_text)

        if "로그인" in clean_text and "해주세요" in clean_text:
            return "🔒 [접근 제한] 로그인 필요한 페이지입니다."

        return clean_text[:4000]  # 너무 길면 잘라냄

    except Exception as e:
        return f"❌ 스크래핑 오류: {e}"


# ======================================
# 3) Naver OpenAPI + scraping
# ======================================

def deep_search_naver_internal(
    refined_query: str,
    needs_recency: bool,
    max_results: int = 5,
) -> Dict[str, Dict[str, str]]:
    """
    Naver OpenAPI(news/webkr/blog) + Playwright로 검색 및 본문 추출 (**동기**).
    반환값: { url: {title, content} } 형태 (tavily와 맞추기 위해 dict 사용)
    """
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return {}

    sections = ["news", "webkr", "blog"]
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }

    sort_opt = "date" if needs_recency else "sim"

    results_by_url: Dict[str, Dict[str, str]] = {}
    per_section = max(1, max_results // len(sections))  # 섹션당 개수 제한

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
                title = re.sub("<[^<]+?>", "", raw_title)
                link = item.get("link", "")

                if not link or link in results_by_url:
                    continue

                full_text = fetch_clean_content(link)

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

        except Exception as e:
            print(f"[Naver API Error] section={section} err={e}")
            continue

        # ✅ async가 아니므로 asyncio.sleep 제거 (원하면 time.sleep 사용 가능)
        # import time; time.sleep(0.3)

    return results_by_url


# ================================
# 4) 요약 (Tavily summarizer 재활용)
# ================================

def summarize_naver_results(results_by_url: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """
    Tavily 파이프라인과 맞추기 위해:
    - raw content를 summarization_model로 요약
    - { url: {title, content(summary)} } 형태로 변환
    """
    summarized: Dict[str, Dict[str, str]] = {}
    MAX_CHARS = 15000

    structured_model = summarization_model.with_structured_output(Summary)

    for url, r in results_by_url.items():
        raw = r["content"]
        truncated = raw[:MAX_CHARS]

        try:
            summary_obj = structured_model.invoke([
                HumanMessage(
                    content=summarize_webpage_prompt.format(
                        webpage_content=truncated,
                        date=get_today_str(),
                    )
                )
            ])
            formatted = (
                f"<summary>\n{summary_obj.summary}\n</summary>\n\n"
                f"<key_excerpts>\n{summary_obj.key_excerpts}\n</key_excerpts>"
            )
        except Exception as e:
            print(f"[Naver summarize error] {e}")
            formatted = raw[:2000] + "..."

        summarized[url] = {
            "title": r["title"],
            "content": formatted,
        }

    return summarized


def format_search_output(summarized_results: Dict[str, Dict[str, str]]) -> str:
    """
    Tavily용 format_search_output 포맷과 동일하게.
    """
    if not summarized_results:
        return "No valid search results found. Please try different search queries or use a different search API."

    formatted_output = "Search results:\n\n"

    for i, (url, result) in enumerate(summarized_results.items(), 1):
        formatted_output += f"\n\n--- SOURCE {i}: {result['title']} ---\n"
        formatted_output += f"URL: {url}\n\n"
        formatted_output += f"SUMMARY:\n{result['content']}\n\n"
        formatted_output += "-" * 80 + "\n"

    return formatted_output


# ==========================
# 5) 최종 Tool: naver_search (sync)
# ==========================

def _naver_search_impl(
    question: str,
    max_results: int = 5,
) -> str:
    # 1) 쿼리 리파인
    refined = refine_kr_search_query(question)
    print(f"[NAVER] original: {question}")
    print(f"[NAVER] refined : {refined.refined_query} (needs_recency={refined.needs_recency})")

    # 2) Naver 검색 + 스크래핑
    raw_results = deep_search_naver_internal(
        refined_query=refined.refined_query,
        needs_recency=refined.needs_recency,
        max_results=max_results,
    )

    # 3) 요약
    summarized = summarize_naver_results(raw_results)

    # 4) 포맷팅
    output = format_search_output(summarized)

    header = (
        f"[NAVER_SEARCH]\n"
        f"refined_query: {refined.refined_query}\n"
        f"needs_recency: {refined.needs_recency}\n\n"
    )
    return header + output


@tool(parse_docstring=True)
def naver_search(
    question: str,
    max_results: Annotated[int, "Maximum number of sources to use"] = 5,
) -> str:
    """
    High-precision Korean web/news/blog search using Naver OpenAPI with **query refinement** and **page scraping**.

    This tool:
    1) Uses an LLM to refine the original Korean question into a concise Naver-friendly search query.
    2) Calls Naver's `news`, `webkr`, and `blog` search APIs with appropriate sorting (by date if time-sensitive).
    3) Visits each result page with Playwright and extracts the main body text (news article / blog post / etc.).
    4) Summarizes the cleaned content using the project's summarization model and `summarize_webpage_prompt`.
    5) Returns a formatted string similar to the Tavily search tool, including URL and SUMMARY for each source.

    Args:
        question: Original Korean question or topic from the user.
        max_results: Maximum number of documents (URLs) to include in the formatted output.

    Returns:
        A formatted string containing refined query, URLs, and summarized content for each result.
        This string is designed to be consumed by the research agent in the same way as the Tavily-based search tool.
    """
    # ✅ 절대 asyncio.run(...) 쓰지 말고, 그냥 sync 구현 호출
    return _naver_search_impl(question, max_results)
