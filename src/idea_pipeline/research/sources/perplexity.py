"""T3: Perplexity sonar-pro — deep web research via OpenAI-compatible endpoint."""
from __future__ import annotations

import json
import os
from typing import Optional

from dotenv import load_dotenv

from idea_pipeline.research.cache import cache_get, cache_set
from idea_pipeline.research.sources.base import (
    RESEARCH_FIELDS,
    clamp,
    parse_json,
    read_prompt,
)

load_dotenv()

_PERPLEXITY_BASE = "https://api.perplexity.ai"
_MODEL = "sonar-pro"


class PerplexityResearcher:
    SOURCE = "perplexity_v1"
    FIDELITY = "tier3"

    def __init__(self):
        from openai import OpenAI
        api_key = os.environ.get("PERPLEXITY_API_KEY", "")
        if not api_key or api_key.startswith("pplx-..."):
            raise RuntimeError(
                "PERPLEXITY_API_KEY not set in .env — "
                "get a key at https://www.perplexity.ai/settings/api"
            )
        self._client = OpenAI(api_key=api_key, base_url=_PERPLEXITY_BASE)
        self._prompt = read_prompt("research_t3_perplexity.txt")

    def research_idea(self, idea_id: str, description: str) -> tuple[dict[str, Optional[int]], str]:
        cache_key = f"t3:{idea_id}"
        cached = cache_get(cache_key, self.SOURCE)
        if cached:
            scores = {f: cached[f] for f in RESEARCH_FIELDS if cached.get(f) is not None}
            return scores, cached.get("narrative", "")

        user_msg = json.dumps({"idea_description": description[:600]}, ensure_ascii=False)
        try:
            resp = self._client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": self._prompt},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=2048,
            )
            text = resp.choices[0].message.content or ""
            data = parse_json(text)
        except Exception as e:
            raise RuntimeError(f"Perplexity API error: {e}") from e

        scores = {f: clamp(data[f]) for f in RESEARCH_FIELDS if data.get(f) is not None}
        narrative = data.get("narrative", "")
        cache_set(cache_key, self.SOURCE, {**scores, "narrative": narrative})
        return scores, narrative
