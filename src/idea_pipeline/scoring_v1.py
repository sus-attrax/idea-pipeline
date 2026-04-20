"""Scoring engine: additive, weighted T0 scoring (vault-only, no LLM).

Formula (v1, weights from config/weights.yaml):
    idea_total = 0.40 * chance_avg + 0.25 * wissen_avg + 0.35 * intrinsic_avg

All raw values inverted (7 - x) so higher score = better idea.
Missing links → contribution from that category is 0 (not penalised further).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from idea_pipeline.schemas import ChanceNote, IdeeNote, WissenNote
from idea_pipeline.vault_io import list_notes, write_note

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WEIGHTS_PATH = _PROJECT_ROOT / "config" / "weights.yaml"


def _load_weights() -> dict:
    all_weights = yaml.safe_load(_WEIGHTS_PATH.read_text(encoding="utf-8"))
    return all_weights["v1"]


def _inv(v: int) -> float:
    return 7.0 - v


def _weighted_avg(values: dict[str, int], weights: dict[str, float]) -> float:
    total_w = 0.0
    total_v = 0.0
    for key, w in weights.items():
        v = values.get(key)
        if v is not None:
            total_v += _inv(v) * w
            total_w += w
    return total_v / total_w if total_w else 0.0


def _score_chance(chance: ChanceNote, weights: dict) -> float:
    return _weighted_avg({
        "granularitaet": chance.granularitaet,
        "urgency": chance.urgency,
        "prevalence": chance.prevalence,
        "impact": chance.impact,
        "personal_experience": chance.personal_experience,
        "market_awareness": chance.market_awareness,
    }, weights["chance"])


def _score_wissen(wissen: WissenNote, weights: dict) -> float:
    return _weighted_avg({
        "enjoyment": wissen.enjoyment,
        "confidence": wissen.confidence,
        "credebility": wissen.credibility,
        "contacts": wissen.contacts,
    }, weights["wissen"])


def _score_intrinsic(idea: IdeeNote, weights: dict) -> float:
    return _weighted_avg({
        "market_size": idea.market_size,
        "market_potential": idea.market_potential,
        "impact": idea.impact,
        "difficulty": idea.difficulty,
        "time_investment": idea.time_investment,
        "innovativeness": idea.innovativeness,
    }, weights["idee_intrinsic"])


@dataclass
class ScoreResult:
    scored: list[tuple[str, float]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def score_vault(
    vault_path: Path,
    dry_run: bool = False,
    top_n: Optional[int] = None,
) -> ScoreResult:
    weights = _load_weights()
    tl = weights["top_level"]

    ideen = list_notes(vault_path, IdeeNote).notes
    chances_by_id = {n.model.id: n.model for n in list_notes(vault_path, ChanceNote).notes}
    wissen_by_id = {n.model.id: n.model for n in list_notes(vault_path, WissenNote).notes}

    result = ScoreResult()
    today = date.today().isoformat()

    for vnote in ideen:
        idea = vnote.model

        linked_chances = [chances_by_id[cid] for cid in idea.chancen if cid in chances_by_id]
        chance_score = (
            sum(_score_chance(c, weights) for c in linked_chances) / len(linked_chances)
            if linked_chances else 0.0
        )

        linked_wissen = [wissen_by_id[wid] for wid in idea.wissen if wid in wissen_by_id]
        wissen_score = (
            sum(_score_wissen(w, weights) for w in linked_wissen) / len(linked_wissen)
            if linked_wissen else 0.0
        )

        intrinsic_score = _score_intrinsic(idea, weights)

        total = round(
            tl["chance_contribution"] * chance_score
            + tl["wissen_contribution"] * wissen_score
            + tl["intrinsic_contribution"] * intrinsic_score,
            4,
        )

        result.scored.append((idea.id, total))

        if not dry_run:
            idea.score = total
            idea.score_breakdown = {
                "chance_score": round(chance_score, 4),
                "wissen_score": round(wissen_score, 4),
                "intrinsic_score": round(intrinsic_score, 4),
                "chance_n": len(linked_chances),
                "wissen_n": len(linked_wissen),
            }
            idea.score_version = "v1"
            idea.scored_at = today
            write_note(vnote)

    result.scored.sort(key=lambda x: x[1], reverse=True)
    if top_n:
        result.scored = result.scored[:top_n]

    return result
