"""T5: Autonomous research loop — 3 focused multi-turn Claude+web_search loops per idea.

Note: Originally spec'd around karpathy/autoresearch, but that repo doesn't exist publicly.
This implements the same concept natively: multi-iteration Claude loops with stop-tool.
Each loop targets a specific angle; results written to vault under research_notes + markdown file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from idea_pipeline.research.cache import cache_get, cache_set
from idea_pipeline.research.sources.base import get_anthropic, read_prompt

_SONNET = "claude-sonnet-4-6"
_MAX_ITER = 5

_LOOPS = [
    {
        "name": "counterarguments",
        "question": "Was sind die stärksten Gründe warum diese Idee scheitert? Fokus auf Markt-Realität, nicht Theorie.",
    },
    {
        "name": "competitors",
        "question": "Finde 3 direkte Konkurrenten mit konkreten Umsatzzahlen oder Funding-Runden. Nenne URLs.",
    },
    {
        "name": "barriers",
        "question": "Welche regulatorischen, technischen oder Go-to-Market-Barrieren existieren konkret für diesen Markt?",
    },
]

_STOP_TOOL = {
    "name": "research_complete",
    "description": "Call when you have enough information to answer the question thoroughly.",
    "input_schema": {
        "type": "object",
        "properties": {
            "findings": {"type": "string", "description": "Complete findings in markdown"},
        },
        "required": ["findings"],
    },
}


class AutoResearcher:
    SOURCE = "autoresearch_v1"
    FIDELITY = "tier5"

    def __init__(self):
        self._llm = get_anthropic()
        self._system = read_prompt("research_t5_loop.txt")

    def research_idea(
        self, idea_id: str, description: str, existing_context: str = ""
    ) -> tuple[dict, str, str]:
        """Return (scores={}, research_notes_markdown, raw_json_for_cache).

        T5 doesn't update market scores — it adds qualitative research_notes.
        """
        cache_key = f"t5:{idea_id}"
        cached = cache_get(cache_key, self.SOURCE)
        if cached:
            return {}, cached.get("research_notes", ""), ""

        results: dict[str, str] = {}
        for loop in _LOOPS:
            findings = self._run_loop(
                loop["name"], loop["question"], description, existing_context
            )
            results[loop["name"]] = findings

        research_notes = self._format_notes(idea_id, description, results)
        cache_set(cache_key, self.SOURCE, {"research_notes": research_notes})
        return {}, research_notes, json.dumps(results, ensure_ascii=False)

    def _run_loop(
        self, loop_name: str, question: str, description: str, context: str
    ) -> str:
        messages = [
            {
                "role": "user",
                "content": (
                    f"Business idea: {description[:400]}\n\n"
                    + (f"Existing research context:\n{context[:800]}\n\n" if context else "")
                    + f"Research question: {question}"
                ),
            }
        ]
        for _ in range(_MAX_ITER):
            try:
                resp = self._llm.messages.create(
                    model=_SONNET,
                    max_tokens=2048,
                    system=self._system,
                    tools=[
                        {"type": "web_search_20250305", "name": "web_search"},
                        _STOP_TOOL,
                    ],
                    messages=messages,
                )
            except Exception:
                break

            # Check for research_complete tool call
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use" and block.name == "research_complete":
                    return block.input.get("findings", "")

            if resp.stop_reason == "end_turn":
                text = next(
                    (b.text for b in resp.content if hasattr(b, "text")), ""
                )
                return text

            # Continue loop
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({
                "role": "user",
                "content": "Continue if you need more information, or call research_complete.",
            })

        return "(no findings)"

    def _format_notes(self, idea_id: str, description: str, results: dict[str, str]) -> str:
        lines = [
            f"# T5 Research Notes: {idea_id}\n",
            f"> {description[:200]}\n",
            "---\n",
        ]
        labels = {
            "counterarguments": "## Gegenargumente / Risiken",
            "competitors":      "## Wettbewerber",
            "barriers":         "## Markteintrittsbarrieren",
        }
        for key, heading in labels.items():
            lines.append(heading)
            lines.append(results.get(key, "(keine Ergebnisse)"))
            lines.append("")
        return "\n".join(lines)
