"""Tests for v2.1 schema additions."""
from idea_pipeline.schemas import IdeeNote, ScoreHistoryEntry


def test_ideenote_defaults_backward_compatible():
    """Existing notes load without new fields — all defaults apply."""
    note = IdeeNote.model_validate({
        "id": "test_idea",
        "database": ["geschaeftsideen"],
    })
    assert note.mastery_leverage == 0.4
    assert note.obsession_leverage == 0.4
    assert note.cross_domain_flag is False
    assert note.capital_class is None
    assert note.regulation_class is None
    assert note.killer_flag is False
    assert note.score_history == []
    assert note.score_v1 is None
    assert note.attractiveness_impact == 6
    assert note.fit_difficulty == 6
    assert note.fit_time_to_first_revenue_months is None
    assert note.willingness_to_pay == 6


def test_score_history_entry():
    entry = ScoreHistoryEntry(
        date="2026-04-19",
        version="v1",
        score=3.91,
        rank=1,
        trigger="baseline",
    )
    assert entry.date == "2026-04-19"
    assert entry.score == 3.91


def test_ideenote_with_score_history():
    note = IdeeNote.model_validate({
        "id": "test",
        "database": ["geschaeftsideen"],
        "score_history": [
            {"date": "2026-04-19", "version": "v1", "score": 3.91, "rank": 1, "trigger": "baseline"},
        ],
    })
    assert len(note.score_history) == 1
    assert note.score_history[0].score == 3.91


def test_capital_class_literal():
    note = IdeeNote.model_validate({
        "id": "test",
        "database": ["geschaeftsideen"],
        "capital_class": "bootstrappable",
    })
    assert note.capital_class == "bootstrappable"


def test_killer_flag_set_directly():
    """killer_flag can be set directly (LLM sets it)."""
    note = IdeeNote.model_validate({
        "id": "test",
        "database": ["geschaeftsideen"],
        "capital_class": "vc_dependent",
        "regulation_class": "high",
        "killer_flag": True,
    })
    assert note.killer_flag is True
