# evaluation/url_scraper.py
import asyncio
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


class URLScraper:
    """
    Playwright를 사용해 URL을 열고 텍스트를 긁어오는 도우미 클래스.

    - 평가 워크플로우에서:
        1) 딥리서치 결과 JSON에서 url 리스트 추출
        2) URLScraper.fetch_many(urls) 호출
        3) 반환된 text를 LLM-as-a-judge 프롬프트에 넣어
           Evidence Quality / Hallucination / URL Validity 평가에 사용
    """

    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 20_000,
        wait_until: str = "networkidle",  # "load", "domcontentloaded", "networkidle"
        max_chars: int = 50_000,
        user_agent: Optional[str] = None,
    ):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.wait_until = wait_until
        self.max_chars = max_chars
        self.user_agent = user_agent

    async def _create_browser(self):
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=self.headless)
        return playwright, browser

    async def fetch_one(self, url: str) -> Dict[str, Any]:
        """
        단일 URL을 열고 내용을 가져온다.

        반환 형식:
        {
            "url": str,
            "ok": bool,                # 2xx~3xx 이면 True
            "status": Optional[int],   # HTTP status code
            "final_url": Optional[str],
            "title": Optional[str],
            "text": str,               # document.body.innerText
            "error": Optional[str],
        }
        """
        playwright = None
        browser = None

        status: Optional[int] = None
        final_url: Optional[str] = None
        title: Optional[str] = None
        text: str = ""
        error: Optional[str] = None

        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(headless=self.headless)

            context_args = {}
            if self.user_agent:
                context_args["user_agent"] = self.user_agent

            context = await browser.new_context(**context_args)
            page = await context.new_page()

            resp = await page.goto(
                url,
                wait_until=self.wait_until,
                timeout=self.timeout_ms,
            )

            if resp:
                status = resp.status
                final_url = resp.url

            # SPA / 동적 로딩을 위해 약간 추가 대기
            await page.wait_for_timeout(2000)

            try:
                title = await page.title()
            except Exception:
                title = None

            # body의 텍스트 추출
            try:
                text = await page.evaluate("() => document.body.innerText || ''")
            except Exception:
                text = ""

            if len(text) > self.max_chars:
                text = text[: self.max_chars]

            await context.close()

        except PlaywrightTimeoutError:
            error = f"Timeout after {self.timeout_ms} ms"
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        finally:
            if browser is not None:
                await browser.close()
            if playwright is not None:
                await playwright.stop()

        return {
            "url": url,
            "ok": status is not None and 200 <= status < 400,
            "status": status,
            "final_url": final_url,
            "title": title,
            "text": text,
            "error": error,
        }

    async def fetch_many(self, urls: List[str], concurrency: int = 3) -> List[Dict[str, Any]]:
        """
        여러 URL을 병렬로 긁어오기.

        - concurrency: 동시에 몇 개까지 열지 (너무 크게 하면 사이트가 막거나 느려질 수 있음)
        """
        semaphore = asyncio.Semaphore(concurrency)
        results: List[Dict[str, Any]] = []

        async def _worker(u: str):
            async with semaphore:
                result = await self.fetch_one(u)
                results.append(result)

        tasks = [asyncio.create_task(_worker(u)) for u in urls]
        await asyncio.gather(*tasks)
        return results
