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


def test_t2_sources_extracted_from_json():
    """claude_search caches a sources list from the LLM JSON response."""
    stored = {}

    def fake_cache_set(query, source, data):
        stored[(query, source)] = data

    mock_llm = MagicMock()
    mock_llm.messages.create.return_value = MagicMock(
        content=[MagicMock(text=json.dumps({
            "market_size": 2,
            "market_potential": 2,
            "prevalence": 3,
            "market_awareness": 3,
            "narrative": "Big market.",
            "sources": [
                {"title": "Gartner Report", "url": "https://gartner.com/report"},
                {"title": "Statista Data",  "url": "https://statista.com/data"},
            ],
        }))]
    )

    with patch("idea_pipeline.research.sources.claude_search.cache_get", return_value=None), \
         patch("idea_pipeline.research.sources.claude_search.cache_set", fake_cache_set), \
         patch("idea_pipeline.research.sources.claude_search.get_anthropic", return_value=mock_llm):
        from idea_pipeline.research.sources.claude_search import ClaudeSearchResearcher
        r = ClaudeSearchResearcher()
        r.research_idea("my-idea", "An idea about X")

    cached = stored.get(("t2:my-idea", "claude_search_v1"))
    assert cached is not None
    assert "sources" in cached
    assert cached["sources"][0]["url"] == "https://gartner.com/report"


def test_t3_citations_and_insights_cached():
    """perplexity researcher captures resp.citations and insights from JSON."""
    stored = {}

    def fake_cache_set(query, source, data):
        stored[(query, source)] = data

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = json.dumps({
        "market_size": 2,
        "market_potential": 2,
        "prevalence": 3,
        "market_awareness": 3,
        "narrative": "Strong market.",
        "insights": {
            "timing": "New EU regulation opens niche.",
            "bottlenecks": "Need 10 enterprise pilots.",
            "risk_flags": ["Regulatory Risk"],
            "risk_justification": "GDPR compliance required.",
            "moat": "Proprietary dataset.",
            "gtm_bottleneck": "First customer: mid-market CFO.",
            "gross_margin": "SaaS >70% structurally fits.",
            "verdict": "Pursue",
            "verdict_reason": "Timing + clear moat.",
            "next_step": "Interview 5 CFOs.",
        },
    })
    mock_resp.citations = [
        "https://perplexity.ai/cite/1",
        "https://perplexity.ai/cite/2",
    ]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp

    with patch("idea_pipeline.research.sources.perplexity.cache_get", return_value=None), \
         patch("idea_pipeline.research.sources.perplexity.cache_set", fake_cache_set), \
         patch("idea_pipeline.research.sources.perplexity.OpenAI", return_value=mock_client):
        from idea_pipeline.research.sources.perplexity import PerplexityResearcher
        r = PerplexityResearcher()
        r.research_idea("pplx-idea", "B2B compliance tool")

    cached = stored.get(("t3:pplx-idea", "perplexity_v1"))
    assert cached is not None
    assert cached["sources"] == ["https://perplexity.ai/cite/1", "https://perplexity.ai/cite/2"]
    assert cached["insights"]["verdict"] == "Pursue"
    assert cached["insights"]["timing"] == "New EU regulation opens niche."


def test_t4_extract_returns_three_tuple():
    """firecrawl _extract returns (scores, narrative, insights) 3-tuple."""
    mock_llm = MagicMock()
    mock_llm.messages.create.return_value = MagicMock(
        content=[MagicMock(text=json.dumps({
            "market_size": 1,
            "market_potential": 2,
            "prevalence": 2,
            "market_awareness": 2,
            "narrative": "Dominant market.",
            "insights": {
                "timing": "Post-COVID shift.",
                "bottlenecks": "Enterprise sales cycle is 18 months.",
                "risk_flags": ["Execution Risk"],
                "risk_justification": "Long sales cycles risk runway.",
                "moat": "Proprietary integrations.",
                "gtm_bottleneck": "First: Head of Operations at 200-person SaaS company.",
                "gross_margin": "SaaS >70% fits.",
                "verdict": "Pursue",
                "verdict_reason": "Strong tailwind + defensible.",
                "next_step": "Run 3 pilot calls with ops leads.",
            },
        }))]
    )

    with patch("idea_pipeline.research.sources.firecrawl.cache_get", return_value=None), \
         patch("idea_pipeline.research.sources.firecrawl.cache_set", lambda q, s, d: None), \
         patch("idea_pipeline.research.sources.firecrawl.get_anthropic", return_value=mock_llm):
        from idea_pipeline.research.sources.firecrawl import FirecrawlResearcher
        r = FirecrawlResearcher.__new__(FirecrawlResearcher)
        r._llm = mock_llm
        r._prompt = "test prompt"
        r._fc = MagicMock()

        result = r._extract(
            "B2B compliance tool",
            "### https://market.com/report\n\nContent here",
            ["https://market.com/report"],
        )

    assert len(result) == 3, "Expected 3-tuple (scores, narrative, insights)"
    scores, narrative, insights = result
    assert insights["verdict"] == "Pursue"
    assert insights["timing"] == "Post-COVID shift."
    assert narrative == "Dominant market."
