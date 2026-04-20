"""Research dispatcher — maps tier number to the right source adapter.

T1 = TavilyResearcher        (snippets, scores only)
T2 = ClaudeSearchResearcher  (Claude + web_search, scores + narrative)
T3 = PerplexityResearcher    (sonar-pro, scores + narrative)
T4 = FirecrawlResearcher     (full-page scrape, scores + narrative, top 5 only)
T5 = AutoResearcher          (3-loop autonomous research, research_notes only)
"""
from __future__ import annotations

from idea_pipeline.research.sources.autoresearch import AutoResearcher
from idea_pipeline.research.sources.base import RESEARCH_FIELDS, tier_level
from idea_pipeline.research.sources.claude_search import ClaudeSearchResearcher
from idea_pipeline.research.sources.firecrawl import MIN_CREDITS, FirecrawlResearcher
from idea_pipeline.research.sources.perplexity import PerplexityResearcher
from idea_pipeline.research.sources.tavily import TavilyResearcher

_RESEARCHERS = {
    1: TavilyResearcher,
    2: ClaudeSearchResearcher,
    3: PerplexityResearcher,
    4: FirecrawlResearcher,
    5: AutoResearcher,
}

TIER_LIMITS = {1: None, 2: 50, 3: 10, 4: 5, 5: 5}


def resolve_tier_limit(tier: int, vault_size: int, explicit_limit: int | None) -> int:
    """Resolve effective research limit for a tier.

    Priority: explicit CLI --limit > config/tiers.yaml > vault_size (no limit).
    """
    if explicit_limit is not None:
        return explicit_limit
    try:
        from idea_pipeline.settings import load_tiers_config
        tiers_cfg = load_tiers_config()
        key = f"t{tier}"
        cfg = tiers_cfg.get(key, {})
        n = cfg.get("limit")
        pct = cfg.get("pct")
        if n is None and pct is None:
            return vault_size
        candidates = []
        if n is not None:
            candidates.append(n)
        if pct is not None:
            candidates.append(int(vault_size * pct))
        return min(candidates)
    except Exception:
        return vault_size


def get_researcher(tier: int):
    cls = _RESEARCHERS.get(tier)
    if cls is None:
        raise ValueError(f"Unknown research tier: {tier}")
    return cls()


__all__ = [
    "get_researcher",
    "resolve_tier_limit",
    "tier_level",
    "TIER_LIMITS",
    "RESEARCH_FIELDS",
    "MIN_CREDITS",
    # backward-compat exports
    "TavilyResearcher",
    "ClaudeSearchResearcher",
    "PerplexityResearcher",
    "FirecrawlResearcher",
    "AutoResearcher",
]
