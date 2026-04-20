"""Tests for generator.py pure functions — no LLM calls, no API calls."""
from pathlib import Path

import pytest

from idea_pipeline.generator import (
    BottleneckResult,
    IdeaCandidate,
    GenerateResult,
    _make_idea_id,
    _select_path_b_candidates,
    _slugify,
)
from idea_pipeline.schemas import IdeeNote
from idea_pipeline.vault_io import VaultNote


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------

def test_slugify_basic():
    assert _slugify("myzel leder") == "myzel_leder"


def test_slugify_special_chars():
    assert _slugify("Myzel-Leder (EU)") == "myzel-leder_eu"


def test_slugify_truncates_at_50():
    long = "a" * 100
    assert len(_slugify(long)) <= 50


# ---------------------------------------------------------------------------
# _make_idea_id
# ---------------------------------------------------------------------------

def test_make_idea_id_format():
    idea_id = _make_idea_id("myzel leder", "Substratproduktion nicht skalierbar")
    assert idea_id.startswith("generated_myzel_leder_")
    assert len(idea_id.split("_")[-1]) == 6  # 6-char hash


def test_make_idea_id_deterministic():
    id1 = _make_idea_id("myzel leder", "Skalierung")
    id2 = _make_idea_id("myzel leder", "Skalierung")
    assert id1 == id2


def test_make_idea_id_different_bottlenecks():
    id1 = _make_idea_id("myzel leder", "Skalierung")
    id2 = _make_idea_id("myzel leder", "Marktakzeptanz")
    assert id1 != id2


# ---------------------------------------------------------------------------
# _select_path_b_candidates
# ---------------------------------------------------------------------------

def _make_vnote(idea_id, market_score, fit_score, vault_path):
    """Helper: create a VaultNote with a given score_breakdown."""
    idea = IdeeNote.model_validate({
        "id": idea_id,
        "database": ["geschaeftsideen"],
        "description": f"Test idea {idea_id}",
    })
    idea.score = 3.0
    idea.score_breakdown = {
        "market_score": market_score,
        "fit_score": fit_score,
        "chance_score": 3.0,
        "attractiveness_score": 3.0,
        "killer_flag": False,
    }
    return VaultNote(model=idea, body="", path=vault_path / f"{idea_id}.md")


def test_select_path_b_returns_high_market_low_fit(tmp_path):
    # 8 ideas: only idea_a should qualify (top-quartile market, bottom-quartile fit)
    vnotes = [
        _make_vnote("idea_a", market_score=6.0, fit_score=1.0, vault_path=tmp_path),
        _make_vnote("idea_b", market_score=5.0, fit_score=2.0, vault_path=tmp_path),
        _make_vnote("idea_c", market_score=4.0, fit_score=3.0, vault_path=tmp_path),
        _make_vnote("idea_d", market_score=3.0, fit_score=4.0, vault_path=tmp_path),
        _make_vnote("idea_e", market_score=2.0, fit_score=5.0, vault_path=tmp_path),
        _make_vnote("idea_f", market_score=1.0, fit_score=6.0, vault_path=tmp_path),
        _make_vnote("idea_g", market_score=5.5, fit_score=3.0, vault_path=tmp_path),
        _make_vnote("idea_h", market_score=4.5, fit_score=4.0, vault_path=tmp_path),
    ]
    result = _select_path_b_candidates(vnotes, limit=5)
    ids = [r[0] for r in result]
    assert "idea_a" in ids


def test_select_path_b_respects_limit(tmp_path):
    vnotes = [
        _make_vnote(f"idea_{i}", market_score=6.0 - i * 0.1, fit_score=1.0, vault_path=tmp_path)
        for i in range(20)
    ]
    result = _select_path_b_candidates(vnotes, limit=3)
    assert len(result) <= 3


def test_select_path_b_empty_when_no_score_breakdown(tmp_path):
    idea = IdeeNote.model_validate({
        "id": "unscored",
        "database": ["geschaeftsideen"],
    })
    vnote = VaultNote(model=idea, body="", path=tmp_path / "unscored.md")
    result = _select_path_b_candidates([vnote], limit=5)
    assert result == []
