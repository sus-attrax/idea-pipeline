"""Scoring engine v2.1 — four dimensions, log scale, Mastery×Obsession knowledge model.

Formula:
    score = 0.35*market + 0.28*fit + 0.20*chance + 0.17*attractiveness

All weights configurable via config/weights.yaml (v2_1 section).
Knowledge signals (mastery/obsession/cross_domain) are computed deterministically
from linked WissenNotes — never from LLM output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from idea_pipeline.schemas import ChanceNote, IdeeNote, ScoreHistoryEntry, WissenNote
from idea_pipeline.vault_io import list_notes, write_note

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WEIGHTS_PATH = _PROJECT_ROOT / "config" / "weights.yaml"

# Log-scale inversion: 1=best(6.0) … 6=worst(1.0)
_INV_LOG_MAP: dict[int, float] = {1: 6.0, 2: 5.2, 3: 4.3, 4: 3.2, 5: 2.0, 6: 1.0}


def inv_log(x: int) -> float:
    """Log-scaled inversion: 1→6.0, 2→5.2, 3→4.3, 4→3.2, 5→2.0, 6→1.0."""
    return _INV_LOG_MAP[x]


def inv_log_normalized(x: int) -> float:
    """inv_log(x) / 6.0 → 0.0 to 1.0."""
    return inv_log(x) / 6.0


def score_time_to_revenue(months: Optional[int]) -> float:
    """Map months-to-first-revenue to 1.0–6.0 score (higher=better)."""
    if months is None:
        return 3.0
    if months < 6:
        return 6.0
    if months <= 12:
        return 5.0
    if months <= 24:
        return 4.0
    if months <= 36:
        return 3.0
    return 2.0


def _load_weights() -> dict:
    return yaml.safe_load(_WEIGHTS_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Knowledge signals — computed from WissenNote fields, never from LLM
# ---------------------------------------------------------------------------

def compute_mastery(wissen: WissenNote) -> float:
    """Domain mastery: weighted avg of confidence, credibility, contacts. 0.0–1.0."""
    cfg = _load_weights()["knowledge_signals"]["mastery_weights"]
    c = inv_log_normalized(wissen.confidence)
    cr = inv_log_normalized(wissen.credibility)
    co = inv_log_normalized(wissen.contacts)
    return cfg["confidence"] * c + cfg["credibility"] * cr + cfg["contacts"] * co


def compute_obsession(wissen: WissenNote) -> float:
    """Domain obsession: enjoyment only. 0.0–1.0."""
    return inv_log_normalized(wissen.enjoyment)


def compute_idea_knowledge_signals(
    linked_wissens: list[WissenNote],
    _all_wissens: list[WissenNote],
) -> dict:
    """Compute mastery_leverage, obsession_leverage, cross_domain_flag for an idea."""
    cfg = _load_weights()["knowledge_signals"]
    floor = cfg["no_links_floor"]

    if not linked_wissens:
        return {
            "mastery_leverage": floor["mastery_leverage"],
            "obsession_leverage": floor["obsession_leverage"],
            "cross_domain_flag": False,
        }

    masteries = [compute_mastery(w) for w in linked_wissens]
    obsessions = [compute_obsession(w) for w in linked_wissens]

    mastery_avg = sum(masteries) / len(masteries)
    obsession_avg = sum(obsessions) / len(obsessions)

    threshold = cfg["cross_domain"]["high_threshold"]
    has_high_mastery = any(m >= threshold for m in masteries)
    has_high_obsession = any(o >= threshold for o in obsessions)

    return {
        "mastery_leverage": round(mastery_avg, 4),
        "obsession_leverage": round(obsession_avg, 4),
        "cross_domain_flag": has_high_mastery and has_high_obsession,
    }


# ---------------------------------------------------------------------------
# Four score dimensions
# ---------------------------------------------------------------------------

def _score_attractiveness(idea: IdeeNote, weights: dict) -> float:
    w = weights["attractiveness"]
    return round(
        w["impact"] * inv_log(idea.attractiveness_impact)
        + w["innovativeness"] * inv_log(idea.attractiveness_innovativeness)
        + w["mission_fit"] * inv_log(idea.attractiveness_mission_fit),
        4,
    )


def _score_fit(idea: IdeeNote, weights: dict) -> float:
    wf = weights["fit"]
    wk = weights["fit_knowledge"]
    cfg = _load_weights()["knowledge_signals"]

    difficulty_s = inv_log(idea.fit_difficulty)
    time_s = score_time_to_revenue(idea.fit_time_to_first_revenue_months)

    mastery_s = idea.mastery_leverage * 6.0
    obsession_s = idea.obsession_leverage * 6.0
    cross_bonus = cfg["cross_domain"]["bonus"] if idea.cross_domain_flag else 0.0

    knowledge_s = min(6.0,
        wk["mastery"] * mastery_s + wk["obsession"] * obsession_s + cross_bonus
    )

    return round(
        wf["difficulty"] * difficulty_s
        + wf["time_to_revenue"] * time_s
        + wf["knowledge"] * knowledge_s,
        4,
    )


def _score_market(idea: IdeeNote, weights: dict) -> float:
    w = weights["market"]
    return round(
        w["market_size"] * inv_log(idea.market_size)
        + w["market_potential"] * inv_log(idea.market_potential)
        + w["willingness_to_pay"] * inv_log(idea.willingness_to_pay)
        + w["market_awareness"] * inv_log(idea.market_awareness),
        4,
    )


def _score_chance(linked_chances: list[ChanceNote], weights: dict) -> float:
    if not linked_chances:
        return 0.0
    wc = weights["chance"]
    scores = []
    for c in linked_chances:
        s = (
            wc["granularitaet"] * inv_log(c.granularitaet)
            + wc["urgency"] * inv_log(c.urgency)
            + wc["prevalence"] * inv_log(c.prevalence)
            + wc["impact"] * inv_log(c.impact)
            + wc["personal_experience"] * inv_log(c.personal_experience)
            + wc["market_awareness"] * inv_log(c.market_awareness)
        )
        scores.append(s)
    return round(sum(scores) / len(scores), 4)


# ---------------------------------------------------------------------------
# Score vault
# ---------------------------------------------------------------------------

@dataclass
class ScoreResult:
    scored: list[tuple[str, float]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def append_to_history(idea: IdeeNote, score: float, rank: int, trigger: str) -> None:
    """Append a new entry to idea.score_history. Never overwrites existing entries."""
    entry = ScoreHistoryEntry(
        date=date.today().isoformat(),
        version="v2.1",
        score=score,
        rank=rank,
        trigger=trigger,
    )
    idea.score_history.append(entry)


def score_vault(
    vault_path: Path,
    dry_run: bool = False,
    top_n: Optional[int] = None,
    trigger: str = "manual",
) -> ScoreResult:
    """Score all ideas using v2.1 formula. Updates mastery/obsession signals in-place."""
    weights_all = _load_weights()
    weights = weights_all["v2_1"]
    tl = weights["top_level"]

    ideen = list_notes(vault_path, IdeeNote).notes
    chances_by_id = {n.model.id: n.model for n in list_notes(vault_path, ChanceNote).notes}
    wissen_by_id = {n.model.id: n.model for n in list_notes(vault_path, WissenNote).notes}
    all_wissens = list(wissen_by_id.values())

    result = ScoreResult()
    today = date.today().isoformat()

    # First pass: compute all scores (needed for global rank)
    scored_tuples: list[tuple[object, float, dict, dict]] = []
    for vnote in ideen:
        idea = vnote.model

        linked_wissens = [wissen_by_id[wid] for wid in idea.wissen if wid in wissen_by_id]
        signals = compute_idea_knowledge_signals(linked_wissens, all_wissens)

        linked_chances = [chances_by_id[cid] for cid in idea.chancen if cid in chances_by_id]

        market_s = _score_market(idea, weights)
        fit_s = _score_fit(idea, weights)
        chance_s = _score_chance(linked_chances, weights)
        attractiveness_s = _score_attractiveness(idea, weights)

        total = round(
            tl["market"] * market_s
            + tl["fit"] * fit_s
            + tl["chance"] * chance_s
            + tl["attractiveness"] * attractiveness_s,
            4,
        )

        breakdown = {
            "market_score": market_s,
            "fit_score": fit_s,
            "chance_score": chance_s,
            "attractiveness_score": attractiveness_s,
            "mastery_leverage": signals["mastery_leverage"],
            "obsession_leverage": signals["obsession_leverage"],
            "cross_domain_flag": signals["cross_domain_flag"],
            "capital_class": idea.capital_class,
            "regulation_class": idea.regulation_class,
            "willingness_to_pay": idea.willingness_to_pay,
            "killer_flag": idea.killer_flag,
            "chance_n": len(linked_chances),
            "wissen_n": len(linked_wissens),
        }
        scored_tuples.append((vnote, total, breakdown, signals))
        result.scored.append((idea.id, total))

    result.scored.sort(key=lambda x: x[1], reverse=True)
    rank_map = {idea_id: i + 1 for i, (idea_id, _) in enumerate(result.scored)}

    if not dry_run:
        for vnote, total, breakdown, signals in scored_tuples:
            idea = vnote.model
            rank = rank_map[idea.id]

            idea.mastery_leverage = signals["mastery_leverage"]
            idea.obsession_leverage = signals["obsession_leverage"]
            idea.cross_domain_flag = signals["cross_domain_flag"]

            idea.score = total
            idea.score_breakdown = breakdown
            idea.score_version = "v2.1"
            idea.scored_at = today

            append_to_history(idea, total, rank, trigger)
            write_note(vnote)

    if top_n:
        result.scored = result.scored[:top_n]

    return result
