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


def get_researcher(tier: int):
    cls = _RESEARCHERS.get(tier)
    if cls is None:
        raise ValueError(f"Unknown research tier: {tier}")
    return cls()


__all__ = [
    "get_researcher",
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
