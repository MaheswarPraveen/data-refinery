"""Scraper API for the Data Refinery agent: POST /crawl {"urls": [...]} -> clean markdown per page.

Protected by the CRAWL_TOKEN secret (sent as a Bearer token), so only the refinery's agent can use it.
"""
import asyncio
import os

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

TOKEN = os.environ.get("CRAWL_TOKEN", "")
app = FastAPI()


class Job(BaseModel):
    urls: list[str]
    max_chars: int = 8000


@app.get("/")
def health():
    return {"ok": True}


@app.post("/crawl")
async def crawl(job: Job, authorization: str = Header("")):
    if not TOKEN or authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "bad token")
    urls = job.urls[:10]                       # a few pages per call keeps the free CPU responsive
    cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=30000)
    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
        results = await crawler.arun_many(urls, config=cfg)
    out = []
    for r in results:
        md = str(r.markdown or "") if r.success else ""
        out.append({"url": r.url, "ok": bool(r.success), "markdown": md[: job.max_chars], "error": None if r.success else str(r.error_message)[:200]})
    return {"pages": out}
