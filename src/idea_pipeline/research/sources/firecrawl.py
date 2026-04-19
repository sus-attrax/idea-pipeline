"""T4: Firecrawl — full-page scrape of market research sources, top 5 only."""
from __future__ import annotations

import json
import os
from typing import Optional

from dotenv import load_dotenv

from idea_pipeline.research.cache import cache_get, cache_set
from idea_pipeline.research.sources.base import (
    RESEARCH_FIELDS,
    clamp,
    get_anthropic,
    parse_json,
    read_prompt,
)

load_dotenv()

_SONNET = "claude-sonnet-4-6"
_FC_SEARCH_SOURCE = "firecrawl_v2_search"
MIN_CREDITS = 20


class FirecrawlResearcher:
    SOURCE = "firecrawl_v2"
    FIDELITY = "tier4"

    def __init__(self):
        from firecrawl import FirecrawlApp
        fc_key = os.environ.get("FIRECRAWL_API_KEY", "")
        if not fc_key or fc_key.startswith("fc-..."):
            raise RuntimeError("FIRECRAWL_API_KEY not set in .env")
        self._fc = FirecrawlApp(api_key=fc_key)
        self._llm = get_anthropic()
        self._prompt = read_prompt("research_t2_extract_v2.txt")

    def remaining_credits(self) -> int:
        try:
            return self._fc.get_credit_usage().remaining_credits or 0
        except Exception:
            return 9999

    def research_idea(self, idea_id: str, description: str) -> tuple[dict[str, Optional[int]], str]:
        cache_key = f"t4:{idea_id}"
        cached = cache_get(cache_key, self.SOURCE)
        if cached:
            scores = {f: cached[f] for f in RESEARCH_FIELDS if cached.get(f) is not None}
            return scores, cached.get("narrative", "")

        urls_with_snippets = self._search_urls(description)
        if not urls_with_snippets:
            return {}, ""

        sections: list[str] = []
        scraped_urls: list[str] = []
        for url, snippet in urls_with_snippets[:2]:
            md = self._scrape(url)
            if md:
                sections.append(f"### {url}\n\n{md[:5000]}")
                scraped_urls.append(url)
            elif snippet:
                sections.append(f"### {url}\n\n{snippet}")
                scraped_urls.append(url)

        if not sections:
            return {}, ""

        scores, narrative = self._extract(description, "\n\n---\n\n".join(sections), scraped_urls)
        cache_set(cache_key, self.SOURCE, {**scores, "narrative": narrative})
        return scores, narrative

    def _search_urls(self, description: str) -> list[tuple[str, str]]:
        query = f"{description[:180]} global market size revenue statistics"
        cached = cache_get(query, _FC_SEARCH_SOURCE)
        if cached:
            return [(r["url"], r.get("description", "")) for r in cached.get("results", [])]
        try:
            result = self._fc.search(query, limit=5)
            results = [
                {"url": item.url, "description": item.description or ""}
                for item in (result.web or [])
                if getattr(item, "url", None)
            ]
        except Exception:
            results = []
        cache_set(query, _FC_SEARCH_SOURCE, {"results": results})
        return [(r["url"], r["description"]) for r in results]

    def _scrape(self, url: str) -> Optional[str]:
        cached = cache_get(url, self.SOURCE)
        if cached:
            return cached.get("markdown")
        try:
            result = self._fc.scrape(url, formats=["markdown"])
            markdown = (result.markdown or "")[:8000] if hasattr(result, "markdown") else ""
        except Exception:
            markdown = None
        cache_set(url, self.SOURCE, {"markdown": markdown})
        return markdown or None

    def _extract(self, description: str, combined_md: str, sources: list[str]) -> tuple[dict, str]:
        payload = {
            "idea_description": description[:400],
            "combined_markdown": combined_md,
            "source_urls": sources,
        }
        try:
            resp = self._llm.messages.create(
                model=_SONNET,
                max_tokens=1024,
                system=self._prompt,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
            data = parse_json(resp.content[0].text)
        except Exception:
            return {}, ""
        scores = {f: clamp(data[f]) for f in RESEARCH_FIELDS if data.get(f) is not None}
        return scores, data.get("narrative", "")
