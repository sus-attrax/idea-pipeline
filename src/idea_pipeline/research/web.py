"""Web research adapters: Tavily (T1) and Firecrawl (T2).

TavilyResearcher   — 4 Tavily searches per idea, Haiku extracts 1-6 scores.
FirecrawlResearcher — finds Destatis/Eurostat via Tavily, scrapes with Firecrawl,
                      Sonnet extracts scores from markdown.
Both use the research cache to avoid redundant API calls.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from idea_pipeline.research.cache import cache_get, cache_set

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PROMPTS_DIR = _PROJECT_ROOT / "config" / "prompts" / "v1"

_HAIKU = "claude-haiku-4-5-20251001"
_SONNET = "claude-sonnet-4-6"

_RESEARCH_FIELDS = ["market_size", "market_potential", "prevalence", "market_awareness"]

_QUERY_TEMPLATES = {
    "market_size":       "{description} market size global annual revenue",
    "market_potential":  "{description} market growth rate CAGR forecast",
    "prevalence":        "{description} problem frequency how many people affected statistics",
    "market_awareness":  "{description} consumer awareness adoption rate survey",
}

_STAT_DOMAINS = ["destatis.de", "eurostat.ec.europa.eu", "statista.com"]


def _get_anthropic():
    from anthropic import Anthropic
    return Anthropic()


def _read_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _parse_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# T1: Tavily
# ---------------------------------------------------------------------------

class TavilyResearcher:
    SOURCE = "tavily_v1"

    def __init__(self):
        from tavily import TavilyClient
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key or api_key.startswith("tvly-..."):
            raise RuntimeError("TAVILY_API_KEY not set in .env")
        self._client = TavilyClient(api_key=api_key)
        self._llm = _get_anthropic()
        self._prompt = _read_prompt("research_t1_extract.txt")

    def research_idea(self, idea_id: str, description: str) -> dict[str, int]:
        """Return {field: score_1_to_6} for all 4 research fields."""
        return {
            field: self._score_field(field, description,
                _QUERY_TEMPLATES[field].format(description=description[:200]))
            for field in _RESEARCH_FIELDS
        }

    def _score_field(self, field: str, description: str, query: str) -> int:
        cached = cache_get(query, self.SOURCE)
        if cached is not None:
            return cached.get("score", 4)

        try:
            results = self._client.search(query=query, search_depth="basic", max_results=5)
            snippets = [
                {
                    "title": r.get("title", ""),
                    "content": r.get("content", "")[:500],
                    "url": r.get("url", ""),
                }
                for r in results.get("results", [])
            ]
        except Exception:
            return 4

        payload = {
            "idea_description": description[:300],
            "field": field,
            "search_results": snippets,
        }
        try:
            resp = self._llm.messages.create(
                model=_HAIKU,
                max_tokens=256,
                system=self._prompt,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
            extracted = _parse_json(resp.content[0].text)
            score = max(1, min(6, int(extracted["score"])))
        except Exception:
            score = 4

        cache_set(query, self.SOURCE, {"score": score, "snippets": snippets})
        return score


# ---------------------------------------------------------------------------
# T2: Firecrawl
# ---------------------------------------------------------------------------

class FirecrawlResearcher:
    SOURCE = "firecrawl_v1"
    TAVILY_SOURCE = "tavily_t2_url_discovery"

    def __init__(self):
        from firecrawl import FirecrawlApp
        from tavily import TavilyClient

        fc_key = os.environ.get("FIRECRAWL_API_KEY", "")
        tv_key = os.environ.get("TAVILY_API_KEY", "")
        if not fc_key or fc_key.startswith("fc-..."):
            raise RuntimeError("FIRECRAWL_API_KEY not set in .env")
        if not tv_key or tv_key.startswith("tvly-..."):
            raise RuntimeError("TAVILY_API_KEY not set in .env")

        self._fc = FirecrawlApp(api_key=fc_key)
        self._tv = TavilyClient(api_key=tv_key)
        self._llm = _get_anthropic()
        self._prompt = _read_prompt("research_t2_extract.txt")

    def research_idea(self, idea_id: str, description: str) -> dict[str, Optional[int]]:
        """Return {field: score_or_None} — only fields with evidence are scored."""
        url = self._find_stat_url(description)
        if url is None:
            return {}
        markdown = self._scrape(url)
        if not markdown:
            return {}
        return self._extract_scores(description, markdown, url)

    def _find_stat_url(self, description: str) -> Optional[str]:
        query = f"{description[:200]} statistics site:destatis.de OR site:eurostat.ec.europa.eu"
        cached = cache_get(query, self.TAVILY_SOURCE)
        if cached:
            return cached.get("url")

        try:
            results = self._tv.search(query=query, search_depth="basic", max_results=3)
            urls = [r.get("url", "") for r in results.get("results", []) if r.get("url")]
            stat_urls = [u for u in urls if any(d in u for d in _STAT_DOMAINS)]
            url = stat_urls[0] if stat_urls else (urls[0] if urls else None)
        except Exception:
            url = None

        cache_set(query, self.TAVILY_SOURCE, {"url": url})
        return url

    def _scrape(self, url: str) -> Optional[str]:
        cached = cache_get(url, self.SOURCE)
        if cached:
            return cached.get("markdown")

        try:
            result = self._fc.scrape_url(url, formats=["markdown"])
            markdown = result.get("markdown", "") if isinstance(result, dict) else ""
            markdown = markdown[:8000]
        except Exception:
            markdown = None

        cache_set(url, self.SOURCE, {"markdown": markdown})
        return markdown

    def _extract_scores(self, description: str, markdown: str, url: str) -> dict[str, Optional[int]]:
        payload = {
            "idea_description": description[:300],
            "scraped_markdown": markdown,
            "source_url": url,
        }
        try:
            resp = self._llm.messages.create(
                model=_SONNET,
                max_tokens=512,
                system=self._prompt,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
            data = _parse_json(resp.content[0].text)
        except Exception:
            return {}

        return {
            field: max(1, min(6, int(data[field])))
            for field in _RESEARCH_FIELDS
            if data.get(field) is not None
        }
