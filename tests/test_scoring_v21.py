"""Unit tests for scoring v2.1 engine."""
import pytest
from pathlib import Path


def test_inv_log_mapping():
    from idea_pipeline.scoring import inv_log
    assert inv_log(1) == 6.0
    assert inv_log(2) == 5.2
    assert inv_log(3) == 4.3
    assert inv_log(4) == 3.2
    assert inv_log(5) == 2.0
    assert inv_log(6) == 1.0


def test_inv_log_normalized_range():
    from idea_pipeline.scoring import inv_log_normalized
    for x in range(1, 7):
        v = inv_log_normalized(x)
        assert 0.0 <= v <= 1.0
    assert inv_log_normalized(1) == pytest.approx(1.0)
    assert inv_log_normalized(6) == pytest.approx(1.0 / 6.0)


def test_score_time_to_revenue():
    from idea_pipeline.scoring import score_time_to_revenue
    assert score_time_to_revenue(None) == pytest.approx(3.0)
    assert score_time_to_revenue(3) == pytest.approx(6.0)
    assert score_time_to_revenue(9) == pytest.approx(5.0)
    assert score_time_to_revenue(18) == pytest.approx(4.0)
    assert score_time_to_revenue(30) == pytest.approx(3.0)
    assert score_time_to_revenue(48) == pytest.approx(2.0)


def _make_wissen(enjoyment=3, confidence=3, credibility=3, contacts=3):
    from idea_pipeline.schemas import WissenNote
    return WissenNote.model_validate({
        "id": "test_wissen",
        "database": ["wissen"],
        "enjoyment": enjoyment,
        "credebility": credibility,
        "confidence": confidence,
        "contacts": contacts,
    })


def test_compute_mastery_high():
    from idea_pipeline.scoring import compute_mastery
    w = _make_wissen(confidence=1, credibility=1, contacts=1)
    m = compute_mastery(w)
    assert 0.0 <= m <= 1.0
    assert m > 0.8


def test_compute_mastery_low():
    from idea_pipeline.scoring import compute_mastery
    w = _make_wissen(confidence=6, credibility=6, contacts=6)
    m = compute_mastery(w)
    assert m < 0.3


def test_compute_obsession_ordering():
    from idea_pipeline.scoring import compute_obsession
    w_high = _make_wissen(enjoyment=1)
    w_low = _make_wissen(enjoyment=6)
    assert compute_obsession(w_high) > compute_obsession(w_low)
    assert 0.0 <= compute_obsession(w_low) <= 1.0


def test_knowledge_signals_no_links():
    from idea_pipeline.scoring import compute_idea_knowledge_signals
    signals = compute_idea_knowledge_signals([], [])
    assert signals["mastery_leverage"] == pytest.approx(0.4)
    assert signals["obsession_leverage"] == pytest.approx(0.4)
    assert signals["cross_domain_flag"] is False


def test_knowledge_signals_cross_domain():
    """High mastery + high obsession → cross_domain_flag=True."""
    from idea_pipeline.scoring import compute_idea_knowledge_signals
    w_mastery = _make_wissen(confidence=1, credibility=1, contacts=1, enjoyment=5)
    w_obsession = _make_wissen(confidence=5, credibility=5, contacts=5, enjoyment=1)
    signals = compute_idea_knowledge_signals([w_mastery, w_obsession], [w_mastery, w_obsession])
    assert signals["cross_domain_flag"] is True


def test_knowledge_signals_no_cross_domain():
    """Only high mastery without high obsession → no cross_domain."""
    from idea_pipeline.scoring import compute_idea_knowledge_signals
    w = _make_wissen(confidence=1, credibility=1, contacts=1, enjoyment=5)
    signals = compute_idea_knowledge_signals([w], [w])
    assert signals["cross_domain_flag"] is False


def test_score_attractiveness_deterministic():
    from idea_pipeline.scoring import _score_attractiveness
    import yaml
    weights = yaml.safe_load(open("config/weights.yaml"))["v2_1"]
    from idea_pipeline.schemas import IdeeNote
    idea = IdeeNote.model_validate({
        "id": "test",
        "database": ["geschaeftsideen"],
        "attractiveness_impact": 2,
        "attractiveness_innovativeness": 2,
        "attractiveness_mission_fit": 1,
    })
    s1 = _score_attractiveness(idea, weights)
    s2 = _score_attractiveness(idea, weights)
    assert s1 == s2
    assert 0.0 < s1 <= 6.0


def test_score_fit_cross_domain_bonus():
    """Cross-domain flag gives a score boost."""
    from idea_pipeline.scoring import _score_fit
    import yaml
    weights = yaml.safe_load(open("config/weights.yaml"))["v2_1"]
    from idea_pipeline.schemas import IdeeNote

    base = IdeeNote.model_validate({
        "id": "base", "database": ["geschaeftsideen"],
        "fit_difficulty": 3, "fit_time_to_first_revenue_months": 12,
        "mastery_leverage": 0.6, "obsession_leverage": 0.6, "cross_domain_flag": False,
    })
    with_cd = IdeeNote.model_validate({
        "id": "cd", "database": ["geschaeftsideen"],
        "fit_difficulty": 3, "fit_time_to_first_revenue_months": 12,
        "mastery_leverage": 0.6, "obsession_leverage": 0.6, "cross_domain_flag": True,
    })
    assert _score_fit(with_cd, weights) > _score_fit(base, weights)


def test_knowledge_signals_single_node_both_high_no_cross_domain():
    """A single wissen node with both high mastery AND high obsession is NOT cross-domain."""
    from idea_pipeline.scoring import compute_idea_knowledge_signals
    # This node has confidence=1 (high mastery) and enjoyment=1 (high obsession) — same node
    w = _make_wissen(confidence=1, credibility=1, contacts=1, enjoyment=1)
    signals = compute_idea_knowledge_signals([w], [w])
    # Single node cannot be cross-domain — needs separate nodes for mastery vs obsession
    assert signals["cross_domain_flag"] is False


def test_score_vault_dry_run():
    """score_vault dry_run returns scored list without writing."""
    from idea_pipeline.scoring import score_vault
    from pathlib import Path
    vault = Path.home() / "vaults/idea-validation"
    if not vault.is_dir():
        pytest.skip("Vault not available")
    result = score_vault(vault, dry_run=True)
    assert len(result.scored) > 0
    # All scores in reasonable range
    for _, s in result.scored:
        assert 0.0 <= s <= 6.0
