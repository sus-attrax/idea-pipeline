"""Web research adapters: Tavily (T1) and Firecrawl (T2).

TavilyResearcher   — 4 Tavily searches per idea, Haiku extracts 1-6 scores.
FirecrawlResearcher — uses Firecrawl search to discover market research pages,
                      scrapes top 2 results, Sonnet extracts 1-6 scores + narrative.
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

_FC_SEARCH_QUERIES = [
    "{description} global market size revenue statistics",
    "{description} market growth rate forecast CAGR",
]

_MIN_CREDITS = 20  # stop T2 if remaining credits drop below this


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
    SOURCE = "firecrawl_v2"
    FC_SEARCH_SOURCE = "firecrawl_v2_search"

    def __init__(self):
        from firecrawl import FirecrawlApp

        fc_key = os.environ.get("FIRECRAWL_API_KEY", "")
        if not fc_key or fc_key.startswith("fc-..."):
            raise RuntimeError("FIRECRAWL_API_KEY not set in .env")

        self._fc = FirecrawlApp(api_key=fc_key)
        self._llm = _get_anthropic()
        self._prompt = _read_prompt("research_t2_extract_v2.txt")

    def remaining_credits(self) -> int:
        try:
            return self._fc.get_credit_usage().remaining_credits or 0
        except Exception:
            return 9999

    def research_idea(self, idea_id: str, description: str) -> tuple[dict[str, Optional[int]], str]:
        """Return (scores_dict, narrative_text). scores may be partial or empty."""
        urls_with_snippets = self._search_urls(description)
        if not urls_with_snippets:
            return {}, ""

        # Scrape top 2 URLs, collect markdown
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

        combined_md = "\n\n---\n\n".join(sections)
        return self._extract(description, combined_md, scraped_urls)

    def _search_urls(self, description: str) -> list[tuple[str, str]]:
        """Return [(url, snippet), ...] from Firecrawl search, cached."""
        query = _FC_SEARCH_QUERIES[0].format(description=description[:180])
        cached = cache_get(query, self.FC_SEARCH_SOURCE)
        if cached:
            return [(r["url"], r.get("description", "")) for r in cached.get("results", [])]

        try:
            result = self._fc.search(query, limit=5)
            items = result.web or []
            results = [
                {"url": item.url, "description": item.description or ""}
                for item in items
                if getattr(item, "url", None)
            ]
        except Exception:
            results = []

        cache_set(query, self.FC_SEARCH_SOURCE, {"results": results})
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

    def _extract(
        self, description: str, combined_md: str, sources: list[str]
    ) -> tuple[dict[str, Optional[int]], str]:
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
            data = _parse_json(resp.content[0].text)
        except Exception:
            return {}, ""

        scores = {
            field: max(1, min(6, int(data[field])))
            for field in _RESEARCH_FIELDS
            if data.get(field) is not None
        }
        narrative = data.get("narrative", "")
        return scores, narrative
