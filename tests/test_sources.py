"""Unit tests for source extraction in T1–T4 research adapters."""
import json
from unittest.mock import MagicMock, patch


def test_t1_sources_aggregated_per_idea():
    """research_idea stores a deduplicated sources list under t1:<idea_id>."""
    stored = {}

    def fake_cache_set(query, source, data):
        stored[(query, source)] = data

    mock_client = MagicMock()
    mock_client.search.return_value = {
        "results": [
            {"title": "Market Report", "content": "big market", "url": "https://example.com/a"},
            {"title": "Growth Study",  "content": "fast growth", "url": "https://example.com/b"},
        ]
    }

    mock_llm = MagicMock()
    mock_llm.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"score": 2}')]
    )

    with patch("idea_pipeline.research.sources.tavily.cache_get", return_value=None), \
         patch("idea_pipeline.research.sources.tavily.cache_set", fake_cache_set), \
         patch("idea_pipeline.research.sources.tavily.TavilyClient", return_value=mock_client), \
         patch("idea_pipeline.research.sources.tavily.get_anthropic", return_value=mock_llm):
        from idea_pipeline.research.sources.tavily import TavilyResearcher
        r = TavilyResearcher()
        r.research_idea("test-idea", "A cool product")

    agg = stored.get(("t1:test-idea", "tavily_v1"))
    assert agg is not None, "Expected aggregate T1 cache entry"
    assert "sources" in agg
    urls = [s["url"] for s in agg["sources"]]
    assert "https://example.com/a" in urls
    assert "https://example.com/b" in urls
