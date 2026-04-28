"""Tests for per-idea insights helpers in report.py."""
import json
from unittest.mock import MagicMock, patch

_SAMPLE_INSIGHTS = {
    "timing": "New EU regulation opened this niche in 2024.",
    "bottlenecks": "Needs 10 pilot customers. Sales cycle is 12 months.",
    "risk_flags": ["PMF-Risk", "Execution Risk"],
    "risk_justification": "PMF unproven at this price point. Sales is hard in this segment.",
    "moat": "Proprietary dataset accumulated over 3 years.",
    "gtm_bottleneck": "First customer: CFO at 100-200 person SaaS company.",
    "gross_margin": "SaaS >70% structurally fits this model.",
    "verdict": "Pursue",
    "verdict_reason": "Clear moat + right timing.",
    "next_step": "Interview 5 CFOs this month.",
}


def test_render_insights_timing_section():
    from idea_pipeline.report import _render_insights_sections
    output = _render_insights_sections(_SAMPLE_INSIGHTS)
    assert "### Timing" in output
    assert "New EU regulation" in output


def test_render_insights_verdict_pursue_has_green_emoji():
    from idea_pipeline.report import _render_insights_sections
    output = _render_insights_sections(_SAMPLE_INSIGHTS)
    assert "### Verdict" in output
    assert "🟢" in output
    assert "Pursue" in output
    assert "Interview 5 CFOs" in output


def test_render_insights_verdict_kill_has_red_emoji():
    from idea_pipeline.report import _render_insights_sections
    output = _render_insights_sections({**_SAMPLE_INSIGHTS, "verdict": "Kill", "verdict_reason": "No market."})
    assert "🔴" in output


def test_render_insights_verdict_validate_has_yellow_emoji():
    from idea_pipeline.report import _render_insights_sections
    output = _render_insights_sections({**_SAMPLE_INSIGHTS, "verdict": "Validate first", "verdict_reason": "Unclear."})
    assert "🟡" in output


def test_render_insights_risk_flags_shown_as_inline_code():
    from idea_pipeline.report import _render_insights_sections
    output = _render_insights_sections(_SAMPLE_INSIGHTS)
    assert "`PMF-Risk`" in output
    assert "`Execution Risk`" in output


def test_render_insights_empty_dict_returns_empty_string():
    from idea_pipeline.report import _render_insights_sections
    output = _render_insights_sections({})
    assert output.strip() == ""


def test_fetch_insights_returns_from_t4_cache():
    t4_cached = {"narrative": "Big market.", "sources": [], "insights": _SAMPLE_INSIGHTS}

    with patch("idea_pipeline.report.cache_get", lambda q, s: t4_cached if q == "t4:idea-x" else None):
        from idea_pipeline.report import _fetch_or_synthesize_insights
        result = _fetch_or_synthesize_insights("idea-x", tier_num=4, narratives={})
    assert result["verdict"] == "Pursue"


def test_fetch_insights_returns_from_t3_cache_when_no_t4():
    t3_cached = {"narrative": "Market.", "sources": [], "insights": {**_SAMPLE_INSIGHTS, "verdict": "Validate first"}}

    def fake_get(query, source):
        if query == "t3:idea-y" and source == "perplexity_v1":
            return t3_cached
        return None

    with patch("idea_pipeline.report.cache_get", fake_get):
        from idea_pipeline.report import _fetch_or_synthesize_insights
        result = _fetch_or_synthesize_insights("idea-y", tier_num=3, narratives={})
    assert result["verdict"] == "Validate first"


def test_fetch_insights_synthesizes_when_no_tier_cache():
    synthesized = {**_SAMPLE_INSIGHTS, "verdict": "Kill"}
    mock_llm = MagicMock()
    mock_llm.messages.create.return_value = MagicMock(
        content=[MagicMock(text=json.dumps(synthesized))]
    )
    stored = {}

    with patch("idea_pipeline.report.cache_get", lambda q, s: None), \
         patch("idea_pipeline.report.cache_set", lambda q, s, d: stored.update({(q, s): d})), \
         patch("idea_pipeline.report.get_anthropic", return_value=mock_llm), \
         patch("idea_pipeline.report.read_prompt", return_value="system prompt"):
        from idea_pipeline.report import _fetch_or_synthesize_insights
        result = _fetch_or_synthesize_insights(
            "idea-z", tier_num=2, narratives={"tier2": "Some T2 narrative text about the market."}
        )
    assert result["verdict"] == "Kill"
    assert ("insights:v1:idea-z", "insights_v1") in stored


def test_fetch_insights_no_narratives_returns_empty():
    with patch("idea_pipeline.report.cache_get", lambda q, s: None):
        from idea_pipeline.report import _fetch_or_synthesize_insights
        result = _fetch_or_synthesize_insights("idea-empty", tier_num=1, narratives={})
    assert result == {}
