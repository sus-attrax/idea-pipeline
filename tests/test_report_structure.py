"""Tests for source helpers and bibliography in report.py."""
import json
from unittest.mock import MagicMock, patch


def _make_cache(data: dict):
    def fake_get(query, source):
        return data.get((query, source))
    return fake_get


def test_fetch_sources_t1_returns_list():
    cache_data = {
        ("t1:my-idea", "tavily_v1"): {
            "sources": [
                {"title": "Report A", "url": "https://a.com"},
                {"title": "Report B", "url": "https://b.com"},
            ]
        }
    }
    with patch("idea_pipeline.report.cache_get", _make_cache(cache_data)):
        from idea_pipeline.report import _fetch_sources
        sources = _fetch_sources("my-idea", "tier1")
    assert len(sources) == 2
    assert sources[0]["url"] == "https://a.com"


def test_fetch_sources_missing_returns_empty():
    with patch("idea_pipeline.report.cache_get", lambda q, s: None):
        from idea_pipeline.report import _fetch_sources
        sources = _fetch_sources("unknown-idea", "tier3")
    assert sources == []


def test_fetch_sources_invalid_tier_returns_empty():
    with patch("idea_pipeline.report.cache_get", lambda q, s: None):
        from idea_pipeline.report import _fetch_sources
        sources = _fetch_sources("idea-x", "tier99")
    assert sources == []


def test_build_bibliography_groups_by_tier():
    idea_a = MagicMock()
    idea_a.id = "idea-a"
    idea_b = MagicMock()
    idea_b.id = "idea-b"

    cache_data = {
        ("t1:idea-a", "tavily_v1"): {"sources": [{"title": "T1 Source", "url": "https://t1.com"}]},
        ("t2:idea-a", "claude_search_v1"): {"sources": [{"title": "T2 Source", "url": "https://t2.com"}]},
        ("t3:idea-b", "perplexity_v1"): {"sources": ["https://t3.com/cite"]},
        ("t4:idea-b", "firecrawl_v2"): {"sources": ["https://t4.com/page"]},
    }
    with patch("idea_pipeline.report.cache_get", _make_cache(cache_data)):
        from idea_pipeline.report import _build_bibliography
        bib = _build_bibliography([idea_a, idea_b])

    assert "Quellen T1" in bib
    assert "https://t1.com" in bib
    assert "Quellen T2" in bib
    assert "https://t2.com" in bib
    assert "Quellen T3" in bib
    assert "https://t3.com/cite" in bib
    assert "Quellen T4" in bib
    assert "https://t4.com/page" in bib


def test_build_bibliography_deduplicates_urls():
    idea_a = MagicMock()
    idea_a.id = "idea-a"
    idea_b = MagicMock()
    idea_b.id = "idea-b"

    cache_data = {
        ("t3:idea-a", "perplexity_v1"): {"sources": ["https://same.com"]},
        ("t3:idea-b", "perplexity_v1"): {"sources": ["https://same.com"]},
    }
    with patch("idea_pipeline.report.cache_get", _make_cache(cache_data)):
        from idea_pipeline.report import _build_bibliography
        bib = _build_bibliography([idea_a, idea_b])

    assert bib.count("https://same.com") == 1


def test_render_meta_section_from_cached_data():
    cached_meta = {
        "executive_summary": "Across all ideas, strong SaaS signals dominate [idea-a].",
        "thematic_sections": {
            "market_timing": "Most ideas target post-2023 regulatory shifts.",
            "technology": "AI automation is the common enabler.",
            "regulatory": "GDPR and EU AI Act create moats.",
            "competition": "Most markets have 2-3 incumbents.",
            "business_model": "SaaS pricing is the dominant model.",
        }
    }
    idea_a = MagicMock()
    idea_a.id = "idea-a"

    with patch("idea_pipeline.report.cache_get", lambda q, s: cached_meta if "report_meta" in q else None):
        from idea_pipeline.report import _fetch_or_build_meta_section
        result = _fetch_or_build_meta_section([idea_a], {})

    assert "## Executive Summary" in result
    assert "strong SaaS signals" in result
    assert "## Thematische Analyse" in result
    assert "### Markt & Timing" in result
    assert "post-2023 regulatory shifts" in result


def test_render_meta_section_empty_dict_returns_empty():
    from idea_pipeline.report import _render_meta_section
    assert _render_meta_section({}) == ""


def test_build_full_report_contains_required_sections():
    """build_full_report output contains the bibliography heading."""
    from idea_pipeline.schemas import IdeeNote
    from idea_pipeline.report import build_full_report
    from pathlib import Path

    idea = IdeeNote(id="slug-1", score=0.8, research_fidelity="tier2")

    mock_llm = MagicMock()
    mock_llm.messages.create.return_value = MagicMock(
        content=[MagicMock(text="{}")]
    )

    with patch("idea_pipeline.report.list_notes") as mock_ln, \
         patch("idea_pipeline.report.cache_get", lambda q, s: None), \
         patch("idea_pipeline.report.cache_set", lambda q, s, d: None), \
         patch("idea_pipeline.report.get_anthropic", return_value=mock_llm), \
         patch("idea_pipeline.report.read_prompt", return_value="prompt"):
        mock_ln.return_value = MagicMock(notes=[])
        output = build_full_report([idea], vault_path=Path("/tmp/fake-vault"))

    assert "# Full Idea Report" in output
    assert "## Rank #1: slug-1" in output
    assert "## Literaturverzeichnis" in output
