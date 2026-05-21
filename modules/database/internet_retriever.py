# internet_retriever.py
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import List, Dict, Any, Set, Iterable
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

# Optional (graceful)
try:
    from readability import Document
except Exception:
    Document = None

try:
    from duckduckgo_search import DDGS
except Exception:
    DDGS = None

try:
    import feedparser
except Exception:
    feedparser = None

try:
    import PyPDF2
except Exception:
    PyPDF2 = None


logger = logging.getLogger("internet_retriever")
logger.setLevel(logging.INFO)


class InternetRetriever:
    """
    FINAL institutional internet retrieval engine.

    DESIGN PRINCIPLES:
    - Zero trust in sources
    - No global failures
    - Deterministic output
    - Fully async
    - Plug-and-play with ingestion pipeline
    """

    MAX_URLS_PER_QUERY = 6
    MAX_TOTAL_DOCS = 25
    FETCH_TIMEOUT = 8.0
    MAX_CONCURRENCY = 10
    MIN_TEXT_LENGTH = 300

    CACHE_DIR = "internet_cache"

    USER_AGENT = (
        "Mozilla/5.0 (InstitutionalRetriever/2.0; +https://example.org)"
    )

    def __init__(
        self,
        enable_readability: bool = True,
        enable_cache: bool = True,
        allow_domains: Iterable[str] | None = None,
        deny_domains: Iterable[str] | None = None,
    ):
        self.enable_readability = enable_readability and Document is not None
        self.enable_cache = enable_cache

        self.allow_domains = set(allow_domains or [])
        self.deny_domains = set(deny_domains or [])

        self._seen_urls: Set[str] = set()
        self._seen_hashes: Set[str] = set()
        self._domain_failures: Dict[str, int] = {}

        os.makedirs(self.CACHE_DIR, exist_ok=True)

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.FETCH_TIMEOUT),
            headers={"User-Agent": self.USER_AGENT},
            follow_redirects=True,
        )

    # ======================================================
    # PUBLIC API
    # ======================================================

    async def retrieve(self, plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        queries = plan.get("queries", [])
        if not queries:
            return []

        urls = self._collect_urls(queries)
        urls = self._filter_urls(urls)

        docs = await self._fetch_all(urls)

        docs = self._quality_filter(docs)
        return docs[: self.MAX_TOTAL_DOCS]

    async def close(self):
        await self._client.aclose()

    # ======================================================
    # URL COLLECTION
    # ======================================================

    def _collect_urls(self, queries: List[str]) -> List[str]:
        urls: List[str] = []

        for q in queries:
            if len(urls) >= self.MAX_TOTAL_DOCS:
                break

            urls.extend(self._search_duckduckgo(q))
            urls.extend(self._search_wikipedia(q))
            urls.extend(self._search_arxiv(q))

        return list(dict.fromkeys(urls))

    def _search_duckduckgo(self, query: str) -> List[str]:
        if not DDGS:
            return []

        urls = []
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=self.MAX_URLS_PER_QUERY):
                    url = r.get("href") or r.get("url")
                    if url:
                        urls.append(url)
        except Exception:
            pass

        return urls

    def _search_wikipedia(self, query: str) -> List[str]:
        return [f"https://en.wikipedia.org/wiki/{query.replace(' ', '_')}"]

    def _search_arxiv(self, query: str) -> List[str]:
        q = query.replace(" ", "+")
        return [f"https://arxiv.org/search/?query={q}&searchtype=all"]

    # ======================================================
    # FILTERING
    # ======================================================

    def _filter_urls(self, urls: List[str]) -> List[str]:
        filtered = []

        for url in urls:
            domain = urlparse(url).netloc.lower()

            if url in self._seen_urls:
                continue

            if self.deny_domains and domain in self.deny_domains:
                continue

            if self.allow_domains and domain not in self.allow_domains:
                continue

            if self._domain_failures.get(domain, 0) > 3:
                continue

            self._seen_urls.add(url)
            filtered.append(url)

        return filtered

    # ======================================================
    # FETCHING
    # ======================================================

    async def _fetch_all(self, urls: List[str]) -> List[Dict[str, Any]]:
        sem = asyncio.Semaphore(self.MAX_CONCURRENCY)

        async def run(url):
            async with sem:
                return await self._fetch_one(url)

        results = await asyncio.gather(
            *(run(u) for u in urls),
            return_exceptions=True,
        )

        return [r for r in results if isinstance(r, dict)]

    async def _fetch_one(self, url: str) -> Dict[str, Any] | None:
        domain = urlparse(url).netloc.lower()

        try:
            if self.enable_cache:
                cached = self._load_cache(url)
                if cached:
                    return cached

            if url.lower().endswith(".pdf"):
                return await self._fetch_pdf(url)

            resp = await self._client.get(url)
            if resp.status_code != 200:
                raise RuntimeError("Bad status")

            text, title = self._extract_html(resp.text)

            if len(text) < self.MIN_TEXT_LENGTH:
                return None

            content_hash = self._hash(text)
            if content_hash in self._seen_hashes:
                return None

            self._seen_hashes.add(content_hash)

            doc = {
                "id": content_hash,
                "title": title,
                "url": url,
                "author": None,
                "text": text,
            }

            self._save_cache(url, doc)
            return doc

        except Exception:
            self._domain_failures[domain] = self._domain_failures.get(domain, 0) + 1
            return None

    async def _fetch_pdf(self, url: str) -> Dict[str, Any] | None:
        if not PyPDF2:
            return None

        resp = await self._client.get(url)
        reader = PyPDF2.PdfReader(resp.content)

        text = " ".join(p.extract_text() or "" for p in reader.pages)
        if len(text) < self.MIN_TEXT_LENGTH:
            return None

        h = self._hash(text)
        if h in self._seen_hashes:
            return None

        self._seen_hashes.add(h)

        return {
            "id": h,
            "title": None,
            "url": url,
            "author": None,
            "text": text,
        }

    # ======================================================
    # EXTRACTION
    # ======================================================

    def _extract_html(self, html: str) -> tuple[str, str | None]:
        if self.enable_readability:
            try:
                html = Document(html).summary(html_partial=True)
            except Exception:
                pass

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title else None
        text = " ".join(soup.get_text(" ").split())
        return text, title

    # ======================================================
    # QUALITY
    # ======================================================

    def _quality_filter(self, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def score(d):
            length_score = min(len(d["text"]) / 3000, 1.0)
            domain = urlparse(d["url"]).netloc
            trust = 1.2 if domain.endswith(".edu") or domain.endswith(".gov") else 1.0
            return length_score * trust

        docs.sort(key=score, reverse=True)
        return docs

    # ======================================================
    # CACHE
    # ======================================================

    def _cache_path(self, url: str) -> str:
        return os.path.join(self.CACHE_DIR, self._hash(url) + ".json")

    def _save_cache(self, url: str, doc: Dict[str, Any]) -> None:
        if not self.enable_cache:
            return
        path = self._cache_path(url)
        try:
            import json
            with open(path, "w", encoding="utf-8") as f:
                json.dump(doc, f)
        except Exception:
            pass

    def _load_cache(self, url: str) -> Dict[str, Any] | None:
        path = self._cache_path(url)
        if not os.path.exists(path):
            return None
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    # ======================================================
    # UTILS
    # ======================================================

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
