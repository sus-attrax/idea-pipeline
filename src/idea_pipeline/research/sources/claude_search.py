"""T2: Claude Sonnet + web_search tool — scores + founder narrative."""
from __future__ import annotations

import json
from typing import Optional

from idea_pipeline.research.cache import cache_get, cache_set
from idea_pipeline.research.sources.base import (
    RESEARCH_FIELDS,
    clamp,
    get_anthropic,
    parse_json,
    read_prompt,
)

_SONNET = "claude-sonnet-4-6"


class ClaudeSearchResearcher:
    SOURCE = "claude_search_v1"
    FIDELITY = "tier2"

    def __init__(self):
        self._llm = get_anthropic()
        self._prompt = read_prompt("research_t2_claude_search.txt")

    def research_idea(self, idea_id: str, description: str) -> tuple[dict[str, Optional[int]], str]:
        cache_key = f"t2:{idea_id}"
        cached = cache_get(cache_key, self.SOURCE)
        if cached:
            scores = {f: cached[f] for f in RESEARCH_FIELDS if cached.get(f) is not None}
            return scores, cached.get("narrative", "")

        user_msg = json.dumps({"idea_description": description[:600]}, ensure_ascii=False)
        try:
            resp = self._llm.messages.create(
                model=_SONNET,
                max_tokens=2048,
                system=self._prompt,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": user_msg}],
            )
            text = next(
                (block.text for block in reversed(resp.content) if hasattr(block, "text")),
                "",
            )
            data = parse_json(text)
        except Exception:
            return {}, ""

        if not isinstance(data, dict):
            return {}, ""

        scores = {f: clamp(data[f]) for f in RESEARCH_FIELDS if data.get(f) is not None}
        narrative = data.get("narrative", "")
        raw_sources = data.get("sources", [])
        sources = raw_sources if isinstance(raw_sources, list) else []
        cache_set(cache_key, self.SOURCE, {**scores, "narrative": narrative, "sources": sources})
        return scores, narrative
