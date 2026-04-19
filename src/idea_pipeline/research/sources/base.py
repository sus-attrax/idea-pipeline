"""Shared constants and utilities for all research source adapters."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
PROMPTS_DIR = _PROJECT_ROOT / "config" / "prompts" / "v1"

RESEARCH_FIELDS = ["market_size", "market_potential", "prevalence", "market_awareness"]

_TIER_ORDER = {"tier1": 1, "tier2": 2, "tier3": 3, "tier4": 4, "tier5": 5}


def tier_level(fidelity: str | None) -> int:
    return _TIER_ORDER.get(fidelity or "", 0)


def read_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def parse_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def get_anthropic():
    from anthropic import Anthropic
    return Anthropic()


def clamp(v: Any) -> int:
    return max(1, min(6, int(v)))
