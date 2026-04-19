"""T1: Tavily — fast snippet-based market scoring (4 queries per idea)."""
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

_HAIKU = "claude-haiku-4-5-20251001"

_QUERY_TEMPLATES = {
    "market_size":      "{description} market size global annual revenue",
    "market_potential": "{description} market growth rate CAGR forecast",
    "prevalence":       "{description} problem frequency how many people affected statistics",
    "market_awareness": "{description} consumer awareness adoption rate survey",
}


class TavilyResearcher:
    SOURCE = "tavily_v1"
    FIDELITY = "tier1"

    def __init__(self):
        from tavily import TavilyClient
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key or api_key.startswith("tvly-..."):
            raise RuntimeError("TAVILY_API_KEY not set in .env")
        self._client = TavilyClient(api_key=api_key)
        self._llm = get_anthropic()
        self._prompt = read_prompt("research_t1_extract.txt")

    def research_idea(self, idea_id: str, description: str) -> tuple[dict[str, int], str]:
        scores = {
            field: self._score_field(
                field, description,
                _QUERY_TEMPLATES[field].format(description=description[:200])
            )
            for field in RESEARCH_FIELDS
        }
        return scores, ""

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
            score = clamp(parse_json(resp.content[0].text)["score"])
        except Exception:
            score = 4

        cache_set(query, self.SOURCE, {"score": score, "snippets": snippets})
        return score
