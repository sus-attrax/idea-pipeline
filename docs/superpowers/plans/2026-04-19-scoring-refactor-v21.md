# Scoring Refactor v2.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the v1 scoring system with v2.1 — two-axis knowledge model (Mastery×Obsession), four score dimensions (market/fit/chance/attractiveness), capital and regulation gates, score history, and a full research cascade (Top 25 → Top 10 → 5) ending in T5 interview briefs.

**Architecture:** v1 scoring is preserved in `scoring_v1.py`; new `scoring.py` implements v2.1. New fields are added to `IdeeNote` with defaults so existing vault notes remain valid. All LLM enrichment is idempotent via field-presence checks. Score history is an append-only list per idea.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, Rich, PyYAML, anthropic SDK (Sonnet 4.6 for enrichment), existing research tiers (Perplexity T3, Firecrawl T4, AutoResearch T5)

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `src/idea_pipeline/schemas.py` | Modify | Add ScoreHistoryEntry + new IdeeNote fields |
| `src/idea_pipeline/scoring.py` → `scoring_v1.py` | Rename | Preserve v1 unchanged |
| `src/idea_pipeline/scoring.py` | Create | v2.1 engine: inv_log, mastery/obsession, 4 dimensions |
| `src/idea_pipeline/enrich_intrinsic.py` | Create | LLM batch rebuild of attractiveness/fit/gates |
| `src/idea_pipeline/portfolio.py` | Create | Greedy+backfill diversity-constrained portfolio selection |
| `src/idea_pipeline/cli.py` | Modify | Add: score --version, enrich-intrinsic, compare-versions, progression, portfolio, brief |
| `config/weights.yaml` | Modify | Add v2_1 section alongside v1 |
| `config/prompts/v2_1/intrinsic_rebuild.txt` | Create | LLM prompt for Step 10 |
| `tests/test_scoring_v21.py` | Create | Unit tests for scoring functions |
| `tests/test_schema_v21.py` | Create | Unit tests for new schema fields |
| `tests/test_portfolio.py` | Create | Unit tests for portfolio selection |

---

## Task 1: Schema — ScoreHistoryEntry + New IdeeNote Fields

**Files:**
- Modify: `src/idea_pipeline/schemas.py`
- Create: `tests/test_schema_v21.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_schema_v21.py`:

```python
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


def test_killer_flag_computed_not_set():
    """killer_flag can be set directly (LLM sets it)."""
    note = IdeeNote.model_validate({
        "id": "test",
        "database": ["geschaeftsideen"],
        "capital_class": "vc_dependent",
        "regulation_class": "high",
        "killer_flag": True,
    })
    assert note.killer_flag is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/homo/idea-pipeline
python -m pytest tests/test_schema_v21.py -v 2>&1 | head -40
```

Expected: `AttributeError: 'IdeeNote' object has no attribute 'mastery_leverage'` or import errors.

- [ ] **Step 3: Add ScoreHistoryEntry to schemas.py**

In `src/idea_pipeline/schemas.py`, add after the imports:

```python
from typing import Annotated, Any, List, Literal, Optional
```

Add this class before `BaseNote`:

```python
class ScoreHistoryEntry(BaseModel):
    """One entry in the per-idea score history log. Append-only."""

    model_config = ConfigDict(populate_by_name=True)

    date: str                    # ISO date string, e.g. "2026-04-19"
    version: str                 # "v1" or "v2.1"
    score: float
    rank: Optional[int] = None   # global rank at time of scoring
    trigger: Optional[str] = None  # what caused this score run
```

- [ ] **Step 4: Extend IdeeNote with new fields**

In `src/idea_pipeline/schemas.py`, replace the `IdeeNote` class body (keeping all existing fields, adding new ones at the end):

```python
class IdeeNote(BaseNote):
    """A business idea — the entity we ultimately rank."""

    first_adopters: list[str] = Field(default_factory=list)
    mass_customers: list[str] = Field(default_factory=list)

    # v1 intrinsic fields — deprecated in v2.1 but kept for backward compat
    market_size: ScoreValue = 6
    market_potential: ScoreValue = 6
    impact: ScoreValue = 6
    difficulty: ScoreValue = 6
    time_investment: ScoreValue = 6
    innovativeness: ScoreValue = 6

    # Links to chance and wissen notes
    chancen: list[str] = Field(default_factory=list)
    wissen: list[str] = Field(default_factory=list)

    notes: Optional[str] = None

    # v2.1 Attractiveness (LLM-populated by enrich-intrinsic)
    attractiveness_impact: ScoreValue = 6
    attractiveness_innovativeness: ScoreValue = 6
    attractiveness_mission_fit: ScoreValue = 6

    # v2.1 Fit — difficulty/time (LLM), knowledge signals (computed)
    fit_difficulty: ScoreValue = 6
    fit_time_to_first_revenue_months: Optional[int] = None  # 1-60

    # Knowledge signals — computed deterministically from wissen links
    mastery_leverage: float = Field(default=0.4, ge=0.0, le=1.0)
    obsession_leverage: float = Field(default=0.4, ge=0.0, le=1.0)
    cross_domain_flag: bool = False

    # Gates (LLM-populated)
    capital_class: Optional[Literal["bootstrappable", "seed", "vc_dependent"]] = None
    regulation_class: Optional[Literal["unregulated", "low", "high"]] = None
    willingness_to_pay: ScoreValue = 6
    killer_flag: bool = False

    # T5 signal
    t5_risk_flag: bool = False

    # Score metadata
    score_v1: Optional[float] = None  # frozen v1 score for comparison
    score_history: List[ScoreHistoryEntry] = Field(default_factory=list)

    @field_validator("chancen", "wissen", mode="before")
    @classmethod
    def _parse_link_lists(cls, v: Any) -> list[str]:
        return _parse_wikilink_list(v)

    @field_validator("first_adopters", "mass_customers", mode="before")
    @classmethod
    def _coerce_customer_lists(cls, v: Any) -> list[str]:
        return _coerce_string_list(v)

    @field_validator("score_history", mode="before")
    @classmethod
    def _parse_score_history(cls, v: Any) -> list:
        if not v:
            return []
        if isinstance(v, list):
            return v
        return []
```

Also update the import at top of `schemas.py` to include `List` and `Literal`:

```python
from typing import Annotated, Any, List, Literal, Optional
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/homo/idea-pipeline
python -m pytest tests/test_schema_v21.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 6: Verify existing vault still reads without errors**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe vault list --type idee 2>&1 | tail -5
```

Expected: 142 ideas listed, no errors.

- [ ] **Step 7: Commit**

```bash
cd /home/homo/idea-pipeline
git add src/idea_pipeline/schemas.py tests/test_schema_v21.py
git commit -m "feat: scoring v2.1 schema — ScoreHistoryEntry + new IdeeNote fields"
```

---

## Task 2: Preserve v1 + New weights.yaml

**Files:**
- Rename: `src/idea_pipeline/scoring.py` → `src/idea_pipeline/scoring_v1.py`
- Modify: `config/weights.yaml`

- [ ] **Step 1: Copy scoring.py to scoring_v1.py (keep original in place)**

```bash
cd /home/homo/idea-pipeline
cp src/idea_pipeline/scoring.py src/idea_pipeline/scoring_v1.py
```

- [ ] **Step 2: Update scoring_v1.py imports to be self-contained**

In `src/idea_pipeline/scoring_v1.py`, the file is already complete. Just verify it imports correctly:

```bash
cd /home/homo/idea-pipeline
python -c "from idea_pipeline.scoring_v1 import score_vault; print('v1 ok')"
```

Expected: `v1 ok`

- [ ] **Step 3: Restructure config/weights.yaml**

Replace entire content of `config/weights.yaml`:

```yaml
# Scoring weights — two versions coexist.
# v1: legacy additive (backward compat)
# v2_1: new 4-dimension model (active)

active_version: v2_1

v1:
  top_level:
    chance_contribution: 0.40
    wissen_contribution: 0.25
    intrinsic_contribution: 0.35
  chance:
    granularitaet: 0.10
    urgency: 0.20
    prevalence: 0.20
    impact: 0.25
    personal_experience: 0.15
    market_awareness: 0.10
  wissen:
    enjoyment: 0.30
    confidence: 0.25
    credebility: 0.25
    contacts: 0.20
  idee_intrinsic:
    market_size: 0.20
    market_potential: 0.20
    impact: 0.20
    difficulty: 0.15
    time_investment: 0.10
    innovativeness: 0.15

v2_1:
  top_level:
    market: 0.35
    fit: 0.28
    chance: 0.20
    attractiveness: 0.17

  attractiveness:
    impact: 0.40
    innovativeness: 0.30
    mission_fit: 0.30

  fit:
    difficulty: 0.25
    time_to_revenue: 0.25
    knowledge: 0.50

  fit_knowledge:
    mastery: 0.35
    obsession: 0.35
    # cross_domain_bonus is additive (+1.0), not a weight

  market:
    market_size: 0.30
    market_potential: 0.30
    willingness_to_pay: 0.25
    market_awareness: 0.15

  chance:
    granularitaet: 0.15
    urgency: 0.20
    prevalence: 0.20
    impact: 0.20
    personal_experience: 0.10
    market_awareness: 0.15

knowledge_signals:
  mastery_weights:
    confidence: 0.50
    credibility: 0.30
    contacts: 0.20
  obsession_weights:
    enjoyment: 1.0
  cross_domain:
    high_threshold: 0.65
    bonus: 1.0
  no_links_floor:
    mastery_leverage: 0.4
    obsession_leverage: 0.4
```

- [ ] **Step 4: Verify weights.yaml parses**

```bash
cd /home/homo/idea-pipeline
python -c "import yaml; d=yaml.safe_load(open('config/weights.yaml')); print(list(d.keys()))"
```

Expected: `['active_version', 'v1', 'v2_1', 'knowledge_signals']`

- [ ] **Step 5: Commit**

```bash
cd /home/homo/idea-pipeline
git add src/idea_pipeline/scoring_v1.py config/weights.yaml
git commit -m "feat: preserve scoring_v1.py, restructure weights.yaml for v1/v2.1 coexistence"
```

---

## Task 3: scoring.py v2.1

**Files:**
- Create: `src/idea_pipeline/scoring.py` (new file, replaces old)
- Create: `tests/test_scoring_v21.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_scoring_v21.py`:

```python
"""Unit tests for scoring v2.1 engine."""
import pytest
from idea_pipeline.scoring import (
    inv_log,
    inv_log_normalized,
    score_time_to_revenue,
    compute_mastery,
    compute_obsession,
    compute_idea_knowledge_signals,
)
from idea_pipeline.schemas import IdeeNote, WissenNote


def test_inv_log_mapping():
    assert inv_log(1) == 6.0
    assert inv_log(2) == 5.2
    assert inv_log(3) == 4.3
    assert inv_log(4) == 3.2
    assert inv_log(5) == 2.0
    assert inv_log(6) == 1.0


def test_inv_log_normalized_range():
    for x in range(1, 7):
        v = inv_log_normalized(x)
        assert 0.0 <= v <= 1.0, f"inv_log_normalized({x}) = {v}"
    assert inv_log_normalized(1) == pytest.approx(1.0)
    assert inv_log_normalized(6) == pytest.approx(1.0 / 6.0)


def test_score_time_to_revenue():
    assert score_time_to_revenue(None) == pytest.approx(3.0)
    assert score_time_to_revenue(3) == pytest.approx(6.0)
    assert score_time_to_revenue(9) == pytest.approx(5.0)
    assert score_time_to_revenue(18) == pytest.approx(4.0)
    assert score_time_to_revenue(30) == pytest.approx(3.0)
    assert score_time_to_revenue(48) == pytest.approx(2.0)


def _make_wissen(enjoyment=3, confidence=3, credibility=3, contacts=3):
    return WissenNote.model_validate({
        "id": "test_wissen",
        "database": ["wissen"],
        "enjoyment": enjoyment,
        "credebility": credibility,
        "confidence": confidence,
        "contacts": contacts,
    })


def test_compute_mastery_range():
    w = _make_wissen(confidence=1, credibility=1, contacts=1)
    m = compute_mastery(w)
    assert 0.0 <= m <= 1.0
    # Best scores → high mastery
    assert m > 0.8

    w_worst = _make_wissen(confidence=6, credibility=6, contacts=6)
    m_worst = compute_mastery(w_worst)
    assert m_worst < 0.3


def test_compute_obsession_range():
    w_high = _make_wissen(enjoyment=1)
    w_low = _make_wissen(enjoyment=6)
    assert compute_obsession(w_high) > compute_obsession(w_low)
    assert 0.0 <= compute_obsession(w_low) <= 1.0


def test_compute_idea_knowledge_signals_no_links():
    signals = compute_idea_knowledge_signals([], [])
    assert signals["mastery_leverage"] == pytest.approx(0.4)
    assert signals["obsession_leverage"] == pytest.approx(0.4)
    assert signals["cross_domain_flag"] is False


def test_compute_idea_knowledge_signals_cross_domain():
    """High mastery + high obsession → cross_domain_flag=True."""
    w_mastery = _make_wissen(confidence=1, credibility=1, contacts=1, enjoyment=5)
    w_obsession = _make_wissen(confidence=5, credibility=5, contacts=5, enjoyment=1)
    signals = compute_idea_knowledge_signals([w_mastery, w_obsession], [w_mastery, w_obsession])
    assert signals["cross_domain_flag"] is True


def test_compute_idea_knowledge_signals_no_cross_domain():
    """Only high mastery without high obsession → no cross_domain."""
    w = _make_wissen(confidence=1, credibility=1, contacts=1, enjoyment=5)
    signals = compute_idea_knowledge_signals([w], [w])
    # mastery high but obsession low → no cross_domain
    assert signals["cross_domain_flag"] is False


def test_full_score_deterministic():
    """Same idea scored twice gives same result."""
    from idea_pipeline.scoring import _score_attractiveness, _score_fit
    import yaml
    from pathlib import Path
    weights = yaml.safe_load((Path("config/weights.yaml")).read_text())["v2_1"]

    idea = IdeeNote.model_validate({
        "id": "test",
        "database": ["geschaeftsideen"],
        "attractiveness_impact": 2,
        "attractiveness_innovativeness": 2,
        "attractiveness_mission_fit": 1,
        "fit_difficulty": 3,
        "fit_time_to_first_revenue_months": 6,
        "mastery_leverage": 0.7,
        "obsession_leverage": 0.6,
        "cross_domain_flag": True,
    })
    s1 = _score_attractiveness(idea, weights)
    s2 = _score_attractiveness(idea, weights)
    assert s1 == s2
    assert 0.0 < s1 <= 6.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/homo/idea-pipeline
python -m pytest tests/test_scoring_v21.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'inv_log' from 'idea_pipeline.scoring'`

- [ ] **Step 3: Write scoring.py v2.1**

Create new `src/idea_pipeline/scoring.py`:

```python
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

# Log-scale inversion mapping: 1=best(6.0) … 6=worst(1.0)
_INV_LOG_MAP: dict[int, float] = {1: 6.0, 2: 5.2, 3: 4.3, 4: 3.2, 5: 2.0, 6: 1.0}


def inv_log(x: int) -> float:
    """Log-scaled inversion: 1→6.0, 2→5.2, 3→4.3, 4→3.2, 5→2.0, 6→1.0."""
    return _INV_LOG_MAP[x]


def inv_log_normalized(x: int) -> float:
    """inv_log(x) / 6.0 → 0.0 to 1.0."""
    return inv_log(x) / 6.0


def score_time_to_revenue(months: Optional[int]) -> float:
    """Map months-to-first-revenue to a 1.0–6.0 score (higher=better)."""
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
    _all_wissens: list[WissenNote],  # unused, kept for future expansion
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
    s = (
        w["impact"] * inv_log(idea.attractiveness_impact)
        + w["innovativeness"] * inv_log(idea.attractiveness_innovativeness)
        + w["mission_fit"] * inv_log(idea.attractiveness_mission_fit)
    )
    return round(s, 4)


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
        wk["mastery"] * mastery_s
        + wk["obsession"] * obsession_s
        + cross_bonus
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
    """Append a score history entry. Never overwrites existing entries."""
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
    """Score all ideas using v2.1 formula. Updates mastery/obsession signals first."""
    weights_all = _load_weights()
    weights = weights_all["v2_1"]
    tl = weights["top_level"]

    ideen = list_notes(vault_path, IdeeNote).notes
    chances_by_id = {n.model.id: n.model for n in list_notes(vault_path, ChanceNote).notes}
    wissen_by_id = {n.model.id: n.model for n in list_notes(vault_path, WissenNote).notes}
    all_wissens = list(wissen_by_id.values())

    result = ScoreResult()
    today = date.today().isoformat()

    # First pass: compute all scores (needed for ranks)
    scored_pairs: list[tuple[object, float, dict]] = []
    for vnote in ideen:
        idea = vnote.model

        # Update knowledge signals deterministically
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
        scored_pairs.append((vnote, total, breakdown, signals))
        result.scored.append((idea.id, total))

    result.scored.sort(key=lambda x: x[1], reverse=True)
    rank_map = {idea_id: i + 1 for i, (idea_id, _) in enumerate(result.scored)}

    if not dry_run:
        for vnote, total, breakdown, signals in scored_pairs:
            idea = vnote.model
            rank = rank_map[idea.id]

            # Update signals on the model
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/homo/idea-pipeline
python -m pytest tests/test_scoring_v21.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Smoke test the CLI still imports**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe hello
```

Expected: `✓ Pipeline lebt.`

- [ ] **Step 6: Commit**

```bash
cd /home/homo/idea-pipeline
git add src/idea_pipeline/scoring.py tests/test_scoring_v21.py
git commit -m "feat: scoring v2.1 — 4 dimensions, inv_log, mastery×obsession, score history"
```

---

## Task 4: CLI — score --version flag + score_v1 baseline

**Files:**
- Modify: `src/idea_pipeline/cli.py`

- [ ] **Step 1: Update the score CLI command**

In `src/idea_pipeline/cli.py`, find the `score` command. Replace it so it accepts `--version`:

First, find the existing score command (search for `@app.command` + "score"):

```bash
grep -n "def score_cmd\|def score\b" /home/homo/idea-pipeline/src/idea_pipeline/cli.py
```

Then replace the score command with:

```python
@app.command("score")
def score_cmd(
    vault: Optional[Path] = _vault_option,
    version: str = typer.Option("v2.1", "--version", help="Scoring version: v1 or v2.1"),
    top_n: Optional[int] = typer.Option(None, "--top", "-n", help="Show only top N ideas"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Compute scores without writing to vault"),
    trigger: str = typer.Option("manual", "--trigger", help="Label for score_history entry"),
    save_as_v1: bool = typer.Option(False, "--save-as-score-v1", help="Also freeze score into score_v1 field"),
) -> None:
    """Score all ideas. Default version: v2.1. Use --version v1 for legacy scoring."""
    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    if version == "v1":
        from idea_pipeline.scoring_v1 import score_vault as score_vault_v1
        from idea_pipeline.vault_io import list_notes, write_note
        from idea_pipeline.schemas import IdeeNote
        from idea_pipeline.schemas import ScoreHistoryEntry
        import datetime

        console.print("[dim]Running v1 scoring...[/dim]")
        result = score_vault_v1(vault_path, dry_run=dry_run, top_n=top_n)

        if save_as_v1 and not dry_run:
            # Write score_v1 + append v1 history entry to each idea
            for vnote in list_notes(vault_path, IdeeNote).notes:
                idea = vnote.model
                if idea.score is not None:
                    idea.score_v1 = idea.score
                    # Add history entry if not already present
                    existing_v1 = [e for e in idea.score_history if e.version == "v1"]
                    if not existing_v1:
                        rank = next((i + 1 for i, (iid, _) in enumerate(result.scored) if iid == idea.id), None)
                        entry = ScoreHistoryEntry(
                            date=datetime.date.today().isoformat(),
                            version="v1",
                            score=idea.score,
                            rank=rank,
                            trigger=trigger,
                        )
                        idea.score_history.append(entry)
                        write_note(vnote)
            console.print(f"[green]✓[/green] score_v1 frozen for {len(result.scored)} ideas")
    elif version == "v2.1":
        from idea_pipeline.scoring import score_vault as score_vault_v21
        console.print("[dim]Running v2.1 scoring...[/dim]")
        result = score_vault_v21(vault_path, dry_run=dry_run, top_n=top_n, trigger=trigger)
    else:
        console.print(f"[red]Unknown version: {version}. Use v1 or v2.1[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Leaderboard (v{version}{'  dry-run' if dry_run else ''})")
    table.add_column("#", style="dim")
    table.add_column("Idea")
    table.add_column("Score", justify="right")
    for rank, (idea_id, score) in enumerate(result.scored, 1):
        table.add_row(str(rank), idea_id, f"{score:.3f}")
    console.print(table)
    console.print(f"\n[bold]{len(result.scored)} ideas scored[/bold]")
```

- [ ] **Step 2: Test score command runs**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe score --dry-run --top 5 2>&1 | head -20
```

Expected: table showing top 5 ideas with v2.1 scores.

- [ ] **Step 3: Test v1 compatibility**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe score --version v1 --dry-run --top 5 2>&1 | head -20
```

Expected: top 5 showing microbial_omega3 at #1 (unchanged from before refactor).

- [ ] **Step 4: Commit**

```bash
cd /home/homo/idea-pipeline
git add src/idea_pipeline/cli.py
git commit -m "feat: score command --version flag (v1 / v2.1), --save-as-score-v1"
```

---

## Task 5: Freeze v1 Baseline

**Goal:** Run v1 scoring once, save scores to `score_v1` field and `score_history` for all 142 ideas before running v2.1.

- [ ] **Step 1: First, run a dry-run to verify no errors**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe score --version v1 --dry-run 2>&1 | tail -5
```

Expected: 142 ideas scored, no errors.

- [ ] **Step 2: Freeze v1 scores**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe score --version v1 --save-as-score-v1 --trigger "baseline_before_v2_1"
```

Expected: `✓ score_v1 frozen for 142 ideas`

- [ ] **Step 3: Verify one idea has score_v1 set**

```bash
cd /home/homo/idea-pipeline
python -c "
from idea_pipeline.vault_io import read_note
from pathlib import Path
vault = Path.home() / 'vaults/idea-validation'
note = read_note(vault / 'microbial_omega3.md')
print('score_v1:', note.model.score_v1)
print('history entries:', len(note.model.score_history))
"
```

Expected: `score_v1: 5.385` (or similar), `history entries: 1`

- [ ] **Step 4: Commit**

```bash
cd /home/homo/idea-pipeline
git add -A
git commit -m "chore: freeze v1 scores as baseline (score_v1 field + score_history)"
```

---

## Task 6: enrich_intrinsic.py — LLM Batch Rebuild

**Files:**
- Create: `config/prompts/v2_1/intrinsic_rebuild.txt`
- Create: `src/idea_pipeline/enrich_intrinsic.py`
- Modify: `src/idea_pipeline/cli.py`

- [ ] **Step 1: Create the LLM prompt**

Create directory and file `config/prompts/v2_1/intrinsic_rebuild.txt`:

```
Du bewertest eine Geschäftsidee nach vier Kriterien und zwei Gates.
Antworte NUR mit validem JSON, kein Markdown drumherum.

KONTEXT: Der User ist ein Solo-Gründer aus Freiburg mit:
- Formelles Wissen: Biotechnologie, Molekularbiologie, Bioreaktoren
- Organisches Wissen: Psychologie, Selbstverbesserung, Fitness, Daygame

---

BEWERTUNGSSCHEMA (alle Werte 1-6, wobei 1=hervorragend, 6=schlecht):

attractiveness.impact: Welchen gesellschaftlichen oder wirtschaftlichen Impact hat die Idee wenn sie groß wird? (1=transformativ, 6=marginal)
attractiveness.innovativeness: Wie neuartig ist der Ansatz? (1=bahnbrechend, 6=commodity)
attractiveness.mission_fit: Wie gut passt die Idee zu einer Mission "Menschen helfen besser zu werden / gesünder zu leben / ihr Potenzial zu entfalten"? (1=perfekter Fit, 6=kein Bezug)

fit.difficulty: Wie schwierig ist die technische/operative Umsetzung für einen Solo-Gründer? (1=einfach mit bekannten Tools, 6=erfordert großes Team oder exotische Expertise)
fit.time_to_first_revenue_months: Wie viele Monate bis zum ersten zahlenden Kunden? (Zahl, kein Score — schätze realistisch für Solo-Gründer, nicht für finanziertes Startup)

capital_class: "bootstrappable" (< €50k bis zum ersten zahlenden Kunden), "seed" (€50k-€500k, EXIST/DBU-tauglich), oder "vc_dependent" (> €500k bevor Revenue möglich ist)
regulation_class: "unregulated" (kein Zulassungsprozess — Software, Bildung, Content, B2B-Tools), "low" (CE-Kennzeichen, DSGVO, ISO — Standard-Compliance, 3-6 Monate), "high" (Novel Food, Arzneimittel, Biozid, Gentechnik, Medizinprodukt — > 1 Jahr + > €100k)
willingness_to_pay: Würden Kunden WIRKLICH dafür zahlen, nicht nur "finden es cool"? (1=hohe Zahlungsbereitschaft, klares Pain, 6=nice-to-have ohne Budget)

reasoning: 1-2 Sätze Begründung für die kritischsten Werte

---

OUTPUT FORMAT (exaktes JSON, alle Felder müssen vorhanden sein):
{
  "id": "<idea_id>",
  "attractiveness": {
    "impact": <1-6>,
    "innovativeness": <1-6>,
    "mission_fit": <1-6>
  },
  "fit": {
    "difficulty": <1-6>,
    "time_to_first_revenue_months": <integer oder null>
  },
  "capital_class": "<bootstrappable|seed|vc_dependent>",
  "regulation_class": "<unregulated|low|high>",
  "willingness_to_pay": <1-6>,
  "reasoning": "<1-2 Sätze>"
}

Beachte: Die Wissens-Signale (mastery, obsession, cross_domain) werden NICHT von dir bewertet — diese werden deterministisch aus dem Vault berechnet.
```

- [ ] **Step 2: Write enrich_intrinsic.py**

Create `src/idea_pipeline/enrich_intrinsic.py`:

```python
"""Step 10: LLM batch rebuild of attractiveness, fit, and gates for all ideas.

Idempotent: skips ideas where attractiveness_impact != 6 (already enriched),
unless --force is passed.

Model: claude-sonnet-4-6
Batch size: 5 ideas per API call
Cost estimate: ~$5 for 142 ideas
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import anthropic

from idea_pipeline.schemas import ChanceNote, IdeeNote, WissenNote
from idea_pipeline.vault_io import list_notes, read_note, write_note

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PROMPT_PATH = _PROJECT_ROOT / "config" / "prompts" / "v2_1" / "intrinsic_rebuild.txt"
_MODEL = "claude-sonnet-4-6"
_BATCH_SIZE = 5


@dataclass
class EnrichIntrinsicResult:
    enriched: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def _build_idea_context(idea: IdeeNote, chances_by_id: dict, wissen_by_id: dict) -> str:
    """Build a text block describing one idea for the LLM."""
    lines = [f"ID: {idea.id}", f"Beschreibung: {idea.description or '(keine)'}"]

    linked_chances = [chances_by_id[c].description for c in idea.chancen if c in chances_by_id]
    if linked_chances:
        lines.append("Verlinkte Chancen/Probleme:")
        for c in linked_chances[:3]:
            if c:
                lines.append(f"  - {c[:200]}")

    linked_wissen = [wissen_by_id[w].description for w in idea.wissen if w in wissen_by_id]
    if linked_wissen:
        lines.append("Wissensgebiete des Founders:")
        for w in linked_wissen[:5]:
            if w:
                lines.append(f"  - {w[:100]}")

    return "\n".join(lines)


def _parse_batch_response(text: str) -> list[dict]:
    """Parse LLM response: either a JSON array or multiple JSON objects."""
    text = text.strip()
    # Try as JSON array first
    if text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    # Try extracting individual JSON objects
    results = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start:i + 1])
                    results.append(obj)
                except json.JSONDecodeError:
                    pass
                start = None
    return results


def _apply_enrichment(idea: IdeeNote, data: dict) -> None:
    """Write LLM output fields to the idea model."""
    attr = data.get("attractiveness", {})
    if "impact" in attr:
        idea.attractiveness_impact = max(1, min(6, int(attr["impact"])))
    if "innovativeness" in attr:
        idea.attractiveness_innovativeness = max(1, min(6, int(attr["innovativeness"])))
    if "mission_fit" in attr:
        idea.attractiveness_mission_fit = max(1, min(6, int(attr["mission_fit"])))

    fit = data.get("fit", {})
    if "difficulty" in fit:
        idea.fit_difficulty = max(1, min(6, int(fit["difficulty"])))
    if "time_to_first_revenue_months" in fit:
        ttr = fit["time_to_first_revenue_months"]
        idea.fit_time_to_first_revenue_months = int(ttr) if ttr is not None else None

    if "capital_class" in data and data["capital_class"] in ("bootstrappable", "seed", "vc_dependent"):
        idea.capital_class = data["capital_class"]
    if "regulation_class" in data and data["regulation_class"] in ("unregulated", "low", "high"):
        idea.regulation_class = data["regulation_class"]
    if "willingness_to_pay" in data:
        idea.willingness_to_pay = max(1, min(6, int(data["willingness_to_pay"])))

    # Set killer_flag
    if idea.capital_class == "vc_dependent" and idea.regulation_class == "high":
        idea.killer_flag = True
    else:
        idea.killer_flag = False


def run_intrinsic_enrich(
    vault_path: Path,
    dry_run: bool = False,
    force: bool = False,
    limit: Optional[int] = None,
    batch_size: int = _BATCH_SIZE,
) -> EnrichIntrinsicResult:
    """Run LLM intrinsic rebuild on all (or limited) ideas."""
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    all_ideen = list_notes(vault_path, IdeeNote).notes
    chances_by_id = {n.model.id: n.model for n in list_notes(vault_path, ChanceNote).notes}
    wissen_by_id = {n.model.id: n.model for n in list_notes(vault_path, WissenNote).notes}

    to_process = []
    result = EnrichIntrinsicResult()

    for vnote in all_ideen:
        idea = vnote.model
        already_done = idea.attractiveness_impact != 6 and not force
        if already_done:
            result.skipped.append(idea.id)
        else:
            to_process.append(vnote)

    if limit:
        to_process = to_process[:limit]

    total = len(to_process)
    if dry_run:
        print(f"[dry-run] Would enrich {total} ideas (skip {len(result.skipped)})")
        for vnote in to_process:
            print(f"  - {vnote.model.id}")
        return result

    client = anthropic.Anthropic()

    # Process in batches, write after each batch
    for batch_start in range(0, total, batch_size):
        batch = to_process[batch_start:batch_start + batch_size]

        ideas_text = "\n\n---\n\n".join(
            _build_idea_context(vnote.model, chances_by_id, wissen_by_id)
            for vnote in batch
        )
        user_msg = (
            f"Bewerte die folgenden {len(batch)} Ideen. "
            f"Antworte mit einem JSON-Array mit {len(batch)} Objekten (einem pro Idee in derselben Reihenfolge).\n\n"
            f"{ideas_text}"
        )

        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = response.content[0].text
            parsed_list = _parse_batch_response(raw)

            if len(parsed_list) != len(batch):
                # Fallback: try to match by ID
                parsed_by_id = {p.get("id"): p for p in parsed_list}
                for vnote in batch:
                    data = parsed_by_id.get(vnote.model.id)
                    if data:
                        _apply_enrichment(vnote.model, data)
                        write_note(vnote)
                        result.enriched.append(vnote.model.id)
                        print(f"[{batch_start + 1}/{total}] {vnote.model.id} ✓ (id-match)")
                    else:
                        result.errors.append((vnote.model.id, "no matching ID in LLM response"))
                        print(f"[{batch_start + 1}/{total}] {vnote.model.id} ✗ (no match)")
            else:
                for vnote, data in zip(batch, parsed_list):
                    _apply_enrichment(vnote.model, data)
                    write_note(vnote)
                    result.enriched.append(vnote.model.id)
                    idx = batch_start + batch.index(vnote) + 1
                    print(f"[{idx}/{total}] {vnote.model.id} ✓")

        except Exception as e:
            for vnote in batch:
                result.errors.append((vnote.model.id, str(e)))
                print(f"[error] {vnote.model.id}: {e}")

    return result
```

- [ ] **Step 3: Wire up CLI command**

In `src/idea_pipeline/cli.py`, add after the existing imports:

```python
from idea_pipeline.enrich_intrinsic import EnrichIntrinsicResult, run_intrinsic_enrich
```

Add new command (before the `if __name__ == "__main__":` at the bottom):

```python
@app.command("enrich-intrinsic")
def enrich_intrinsic_cmd(
    vault: Optional[Path] = _vault_option,
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without API calls"),
    force: bool = typer.Option(False, "--force", help="Re-enrich already-enriched ideas"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="Process only N ideas"),
) -> None:
    """Step 10: LLM batch rebuild of attractiveness, fit, and gates for all ideas.

    Idempotent: skips ideas already enriched (attractiveness_impact != 6), unless --force.
    Costs ~$5 for all 142 ideas (Sonnet 4.6, batch size 5).
    """
    import math

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    from idea_pipeline.vault_io import list_notes
    from idea_pipeline.schemas import IdeeNote

    all_ideen = list_notes(vault_path, IdeeNote).notes
    to_enrich = [n for n in all_ideen if n.model.attractiveness_impact == 6 or force]
    effective = min(len(to_enrich), limit or len(to_enrich))

    estimated_cost = (math.ceil(effective / 5)) * 0.15
    console.print(f"[bold]enrich-intrinsic[/bold]  {effective} ideas · ~${estimated_cost:.2f} estimated")

    if not dry_run and estimated_cost > 1.0:
        confirm = typer.confirm(f"Run LLM enrichment for {effective} ideas (~${estimated_cost:.2f})?")
        if not confirm:
            console.print("Aborted.")
            raise typer.Exit(0)

    result = run_intrinsic_enrich(
        vault_path,
        dry_run=dry_run,
        force=force,
        limit=limit,
    )
    console.print(
        f"\n[green]✓[/green] enriched={len(result.enriched)} "
        f"skipped={len(result.skipped)} "
        f"errors={len(result.errors)}"
    )
    if result.errors:
        for idea_id, msg in result.errors:
            console.print(f"  [red]✗[/red] {idea_id}: {msg}")
```

- [ ] **Step 4: Test dry-run**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe enrich-intrinsic --dry-run --limit 3
```

Expected: lists 3 idea IDs that would be enriched, no API calls.

- [ ] **Step 5: Commit**

```bash
cd /home/homo/idea-pipeline
git add config/prompts/v2_1/intrinsic_rebuild.txt src/idea_pipeline/enrich_intrinsic.py src/idea_pipeline/cli.py
git commit -m "feat: intrinsic rebuild via LLM (v2.1) — enrich-intrinsic command"
```

---

## Task 7: Run Intrinsic Rebuild (Real API Call)

**Note:** This task requires API access and costs ~$5. Run after user confirms.

- [ ] **Step 1: Test with 3 ideas first**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe enrich-intrinsic --limit 3
```

Expected: 3 ideas enriched, check one:

```bash
python -c "
from idea_pipeline.vault_io import read_note
from pathlib import Path
vault = Path.home() / 'vaults/idea-validation'
note = read_note(vault / 'microbial_omega3.md')
m = note.model
print(f'attractiveness_impact={m.attractiveness_impact}')
print(f'capital_class={m.capital_class}')
print(f'regulation_class={m.regulation_class}')
print(f'killer_flag={m.killer_flag}')
"
```

Expected: values are no longer 6/None.

- [ ] **Step 2: Run full vault enrichment**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe enrich-intrinsic
```

Expected: ~29 batches, ~$4-5, all 142 ideas enriched.

- [ ] **Step 3: Commit**

```bash
cd /home/homo/idea-pipeline
git add -A
git commit -m "feat: intrinsic rebuild via LLM (v2.1) — full vault run complete"
```

---

## Task 8: v2.1 Leaderboard + compare-versions + progression

**Files:**
- Modify: `src/idea_pipeline/cli.py`

- [ ] **Step 1: Run v2.1 scoring on full vault**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe score --version v2.1 --trigger "intrinsic_rebuild" 2>&1 | tail -10
```

Expected: 142 ideas scored.

- [ ] **Step 2: Generate v2.1 leaderboard**

In `src/idea_pipeline/cli.py`, update the `report` command to accept a `--version` flag and output `LEADERBOARD_V2_1.md`. Add the following logic to the existing `report_cmd`:

Find the existing `report_cmd` function and replace it:

```python
@app.command("report")
def report_cmd(
    vault: Optional[Path] = _vault_option,
    out: Path = typer.Option(Path("LEADERBOARD.md"), "--out", "-o", help="Output markdown file"),
    version: str = typer.Option("v2.1", "--version", help="v1 or v2.1 column layout"),
) -> None:
    """Write a ranked markdown leaderboard of all scored ideas."""
    import datetime
    import yaml

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    ideas: list[dict] = []
    for f in vault_path.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        try:
            meta = yaml.safe_load(parts[1]) or {}
        except Exception:
            continue
        db = meta.get("database") or []
        if isinstance(db, str):
            db = [db]
        if not any("geschaeftsideen" in str(d) for d in db):
            continue
        if meta.get("score") is None:
            continue
        ideas.append({"id": f.stem, **meta})

    ideas.sort(key=lambda x: x.get("score", 0), reverse=True)

    def fmt(v, fallback="—"):
        return str(v) if v is not None else fallback

    def mastery_bar(v):
        if v is None:
            return "·"
        v = float(v)
        if v >= 0.75:
            return "▇"
        if v >= 0.50:
            return "▅"
        if v >= 0.25:
            return "▃"
        return "▁"

    today = datetime.date.today().isoformat()

    if version == "v2.1":
        lines = [
            f"# Idea Leaderboard v2.1 — {today}",
            "",
            f"**{len(ideas)} ideas scored** · generated by `ideapipe report --version v2.1`",
            "",
            "| # | Idea | Score | Cap | Reg | Kill | Mast | Obs | CD | WTP | mkt↑ | fit↑ | ch↑ | att↑ |",
            "|---|------|------:|:---:|:---:|:----:|:----:|:---:|:--:|:---:|:----:|:----:|:---:|:----:|",
        ]
        for rank, m in enumerate(ideas, 1):
            sb = m.get("score_breakdown") or {}
            cd = "✓" if sb.get("cross_domain_flag") else "·"
            kill = "💀" if sb.get("killer_flag") else "·"
            lines.append(
                f"| {rank} "
                f"| {m['id']} "
                f"| {m.get('score', 0):.3f} "
                f"| {fmt(sb.get('capital_class'), '—')[:4]} "
                f"| {fmt(sb.get('regulation_class'), '—')[:4]} "
                f"| {kill} "
                f"| {mastery_bar(sb.get('mastery_leverage'))} "
                f"| {mastery_bar(sb.get('obsession_leverage'))} "
                f"| {cd} "
                f"| {fmt(sb.get('willingness_to_pay'))} "
                f"| {sb.get('market_score', 0):.1f} "
                f"| {sb.get('fit_score', 0):.1f} "
                f"| {sb.get('chance_score', 0):.1f} "
                f"| {sb.get('attractiveness_score', 0):.1f} |"
            )
        lines += [
            "",
            "---",
            "",
            "**Column guide**",
            "- **Cap**: capital_class (boot=bootstrappable, seed=seed, vc=vc_dependent)",
            "- **Reg**: regulation_class (un=unregulated, lo=low, hi=high)",
            "- **Kill**: 💀 = killer_flag (vc_dependent + high regulation)",
            "- **Mast/Obs**: mastery/obsession leverage ▁▃▅▇ (0.0→1.0)",
            "- **CD**: cross_domain_flag ✓ = true",
            "- **WTP**: willingness_to_pay (1=high, 6=low)",
            "",
        ]
    else:
        # v1 layout (existing)
        def tier_badge(t):
            return {"tier1": "T1", "tier2": "T2", "tier3": "T3", "tier4": "T4", "tier5": "T5"}.get(t or "", "—")

        def wissen_str(meta):
            links = meta.get("wissen") or []
            if isinstance(links, str):
                links = [links]
            names = [str(w).strip("[]").replace("[[", "").replace("]]", "") for w in links]
            return ", ".join(names) if names else "—"

        lines = [
            f"# Idea Leaderboard — {today}",
            "",
            f"**{len(ideas)} ideas scored** · generated by `ideapipe report`",
            "",
            "| # | Idea | Score | Tier | mSz | mPot | prev | mAw | ch↑ | ws↑ | intr↑ | Wissen |",
            "|---|------|------:|:----:|:---:|:----:|:----:|:---:|:---:|:---:|:-----:|--------|",
        ]
        for rank, m in enumerate(ideas, 1):
            sb = m.get("score_breakdown") or {}
            lines.append(
                f"| {rank} | {m['id']} | {m.get('score', 0):.3f} "
                f"| {tier_badge(m.get('research_fidelity'))} "
                f"| {fmt(m.get('market_size'))} | {fmt(m.get('market_potential'))} "
                f"| {fmt(m.get('prevalence'))} | {fmt(m.get('market_awareness'))} "
                f"| {sb.get('chance_score', 0):.1f} | {sb.get('wissen_score', 0):.1f} "
                f"| {sb.get('intrinsic_score', 0):.1f} | {wissen_str(m)} |"
            )

    if not out.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent.parent
        out = repo_root / out

    out.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]✓[/green] {out}  ({len(ideas)} ideas)")
```

- [ ] **Step 3: Generate v2.1 leaderboard**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe report --version v2.1 --out LEADERBOARD_V2_1.md
```

Expected: `LEADERBOARD_V2_1.md` created with 142 rows and new columns.

- [ ] **Step 4: Add compare-versions command to cli.py**

Add after `report_cmd`:

```python
@app.command("compare-versions")
def compare_versions_cmd(
    vault: Optional[Path] = _vault_option,
) -> None:
    """Compare v1 vs v2.1 scores — show rank movements."""
    import datetime
    import yaml

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    from idea_pipeline.vault_io import list_notes
    from idea_pipeline.schemas import IdeeNote

    ideas = list_notes(vault_path, IdeeNote).notes
    rows = []
    for vnote in ideas:
        idea = vnote.model
        if idea.score is None or idea.score_v1 is None:
            continue
        rows.append({
            "id": idea.id,
            "score_v1": idea.score_v1,
            "score_v21": idea.score,
        })

    # Compute ranks
    v1_sorted = sorted(rows, key=lambda x: x["score_v1"], reverse=True)
    v21_sorted = sorted(rows, key=lambda x: x["score_v21"], reverse=True)
    v1_rank = {r["id"]: i + 1 for i, r in enumerate(v1_sorted)}
    v21_rank = {r["id"]: i + 1 for i, r in enumerate(v21_sorted)}

    for r in rows:
        r["v1_rank"] = v1_rank[r["id"]]
        r["v21_rank"] = v21_rank[r["id"]]
        r["delta"] = r["v1_rank"] - r["v21_rank"]  # positive = moved up in v2.1

    rows.sort(key=lambda x: abs(x["delta"]), reverse=True)

    today = datetime.date.today().isoformat()
    lines = [
        f"# v1 vs v2.1 Score Comparison — {today}",
        "",
        f"**{len(rows)} ideas compared**",
        "",
        "| Idea | v1 Score | v1 Rank | v2.1 Score | v2.1 Rank | Δ Rank |",
        "|------|:--------:|:-------:|:----------:|:---------:|:------:|",
    ]
    for r in rows:
        delta_str = f"+{r['delta']}" if r["delta"] > 0 else str(r["delta"])
        lines.append(
            f"| {r['id']} "
            f"| {r['score_v1']:.3f} "
            f"| #{r['v1_rank']} "
            f"| {r['score_v21']:.3f} "
            f"| #{r['v21_rank']} "
            f"| {delta_str} |"
        )

    lines += ["", "---", ""]
    lines += ["## Top 10 Aufsteiger (v1→v2.1)"]
    risers = sorted(rows, key=lambda x: x["delta"], reverse=True)[:10]
    for r in risers:
        lines.append(f"- **{r['id']}**: #{r['v1_rank']} → #{r['v21_rank']} (+{r['delta']})")

    lines += ["", "## Top 10 Absteiger (v1→v2.1)"]
    fallers = sorted(rows, key=lambda x: x["delta"])[:10]
    for r in fallers:
        lines.append(f"- **{r['id']}**: #{r['v1_rank']} → #{r['v21_rank']} ({r['delta']})")

    reports_dir = Path(__file__).resolve().parent.parent.parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    out = reports_dir / f"v1_vs_v2_1_comparison_{today}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]✓[/green] {out}")
```

- [ ] **Step 5: Add progression command to cli.py**

```python
@app.command("progression")
def progression_cmd(
    vault: Optional[Path] = _vault_option,
    idea_id: Optional[str] = typer.Option(None, "--idea-id", help="Show history for one idea"),
    all_ideas: bool = typer.Option(False, "--all", help="Show all ideas with score history"),
    top: int = typer.Option(20, "--top", help="With --all: show top N ideas"),
) -> None:
    """Show score progression over time from score_history."""
    import datetime
    from idea_pipeline.vault_io import list_notes
    from idea_pipeline.schemas import IdeeNote

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    ideas = list_notes(vault_path, IdeeNote).notes

    if idea_id:
        match = next((n for n in ideas if n.model.id == idea_id), None)
        if not match:
            console.print(f"[red]Idea not found:[/red] {idea_id}")
            raise typer.Exit(1)
        history = match.model.score_history
        if not history:
            console.print(f"No score history for {idea_id}")
            raise typer.Exit(0)
        table = Table(title=f"Score history: {idea_id}")
        table.add_column("Date")
        table.add_column("Version")
        table.add_column("Score", justify="right")
        table.add_column("Rank", justify="right")
        table.add_column("Trigger")
        for entry in history:
            table.add_row(
                entry.date,
                entry.version,
                f"{entry.score:.3f}",
                str(entry.rank or "—"),
                entry.trigger or "—",
            )
        console.print(table)
    elif all_ideas:
        # Show all ideas that have history, sorted by current score
        rows = [n for n in ideas if n.model.score_history]
        rows.sort(key=lambda n: n.model.score or 0, reverse=True)
        rows = rows[:top]

        table = Table(title=f"Score progression — top {top}")
        table.add_column("Idea")
        # Collect all unique versions present
        all_versions = []
        for n in rows:
            for e in n.model.score_history:
                if e.version not in all_versions:
                    all_versions.append(e.version)

        for v in all_versions:
            table.add_column(f"Score ({v})", justify="right")
            table.add_column(f"Rank ({v})", justify="right")

        for n in rows:
            hist_by_version = {}
            for e in n.model.score_history:
                hist_by_version[e.version] = e
            row_data = [n.model.id]
            for v in all_versions:
                e = hist_by_version.get(v)
                row_data.append(f"{e.score:.3f}" if e else "—")
                row_data.append(f"#{e.rank}" if e and e.rank else "—")
            table.add_row(*row_data)

        console.print(table)
    else:
        console.print("Specify --idea-id X or --all")
```

- [ ] **Step 6: Generate comparison report**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe compare-versions
```

Expected: `reports/v1_vs_v2_1_comparison_2026-XX-XX.md` created.

- [ ] **Step 7: Commit**

```bash
cd /home/homo/idea-pipeline
git add src/idea_pipeline/cli.py LEADERBOARD_V2_1.md reports/
git commit -m "feat: v2.1 leaderboard + compare-versions + progression commands"
```

---

## Task 9: T3+T4 Research Cascade on Top 25

**Files:**
- Modify: `src/idea_pipeline/cli.py` (research command — only selection logic changes)

- [ ] **Step 1: Check existing research command**

```bash
grep -n "def research_cmd\|effective_limit\|top_n" /home/homo/idea-pipeline/src/idea_pipeline/cli.py | head -20
```

- [ ] **Step 2: Update research command to accept --top flag**

Find the `research_cmd` function. The key change is: when `--top N` is passed, select the top N ideas from the current v2.1 leaderboard. Add `--top` option:

Locate the definition of `research_cmd` and add this parameter:

```python
top_from_score: Optional[int] = typer.Option(None, "--top", help="Research top N ideas by current v2.1 score"),
```

And in the body, before the `effective_limit` assignment, add:

```python
    # If --top specified, get top N idea IDs from current score ranking
    top_ids: Optional[set] = None
    if top_from_score:
        from idea_pipeline.vault_io import list_notes
        from idea_pipeline.schemas import IdeeNote
        import operator
        ranked = sorted(
            [(n.model.id, n.model.score or 0) for n in list_notes(vault_path, IdeeNote).notes],
            key=operator.itemgetter(1),
            reverse=True,
        )
        top_ids = {iid for iid, _ in ranked[:top_from_score]}
```

Then in the loop where ideas are filtered (look for `if idea.research_fidelity`), add:

```python
        if top_ids is not None and idea.id not in top_ids:
            skipped += 1
            continue
```

- [ ] **Step 3: Test T3 dry-run on top 25**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe research --tier 3 --top 25 --dry-run 2>&1 | head -20
```

Expected: lists 25 idea IDs that would be researched.

- [ ] **Step 4: Run T3 on top 25 (real, costs ~€5)**

User must confirm. Run:

```bash
cd /home/homo/idea-pipeline
python -m ideapipe research --tier 3 --top 25
```

- [ ] **Step 5: Run T4 on top 25 (real, ~2500 Firecrawl credits)**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe research --tier 4 --top 25
```

- [ ] **Step 6: Re-score and generate post-T3T4 leaderboard**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe score --version v2.1 --trigger "t3_t4_research"
python -m ideapipe report --version v2.1 --out LEADERBOARD_V2_1_post_t3t4.md
```

- [ ] **Step 7: Commit**

```bash
cd /home/homo/idea-pipeline
git add LEADERBOARD_V2_1_post_t3t4.md
git commit -m "feat: t3+t4 research cascade on top 25, post-research leaderboard"
```

---

## Task 10: Portfolio Selection — Top 10

**Files:**
- Create: `src/idea_pipeline/portfolio.py`
- Create: `tests/test_portfolio.py`
- Modify: `src/idea_pipeline/cli.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_portfolio.py`:

```python
"""Tests for portfolio selection with diversity constraints."""
from idea_pipeline.portfolio import (
    PortfolioConstraints,
    select_portfolio_from_ideas,
    PortfolioResult,
)
from idea_pipeline.schemas import IdeeNote


def _make_idea(
    idea_id: str,
    score: float,
    capital_class="bootstrappable",
    regulation_class="unregulated",
    cross_domain_flag=False,
    mastery_leverage=0.4,
    obsession_leverage=0.4,
    killer_flag=False,
    time_to_revenue=6,
):
    idea = IdeeNote.model_validate({"id": idea_id, "database": ["geschaeftsideen"]})
    idea.score = score
    idea.capital_class = capital_class
    idea.regulation_class = regulation_class
    idea.cross_domain_flag = cross_domain_flag
    idea.mastery_leverage = mastery_leverage
    idea.obsession_leverage = obsession_leverage
    idea.killer_flag = killer_flag
    idea.fit_time_to_first_revenue_months = time_to_revenue
    return idea


def _make_diverse_pool():
    """Pool of 20 ideas with different profiles to test constraint satisfaction."""
    return [
        _make_idea("boot1", 5.0, capital_class="bootstrappable", regulation_class="unregulated"),
        _make_idea("boot2", 4.9, capital_class="bootstrappable", regulation_class="low"),
        _make_idea("boot3", 4.8, capital_class="bootstrappable", regulation_class="unregulated"),
        _make_idea("seed1", 4.7, capital_class="seed", regulation_class="unregulated", cross_domain_flag=True, mastery_leverage=0.7, obsession_leverage=0.7),
        _make_idea("seed2", 4.6, capital_class="seed", regulation_class="low", cross_domain_flag=True, mastery_leverage=0.7, obsession_leverage=0.7),
        _make_idea("seed3", 4.5, capital_class="seed", regulation_class="unregulated", cross_domain_flag=True, mastery_leverage=0.7, obsession_leverage=0.7),
        _make_idea("vc1", 4.4, capital_class="vc_dependent", regulation_class="low"),
        _make_idea("vc2", 4.3, capital_class="vc_dependent", regulation_class="unregulated"),
        _make_idea("vc3", 4.2, capital_class="vc_dependent", regulation_class="unregulated"),
        _make_idea("high_reg1", 4.1, capital_class="seed", regulation_class="high"),
        _make_idea("high_reg2", 4.0, capital_class="bootstrappable", regulation_class="high"),
        _make_idea("mastery1", 3.9, capital_class="seed", regulation_class="low", mastery_leverage=0.8),
        _make_idea("mastery2", 3.8, capital_class="bootstrappable", regulation_class="unregulated", mastery_leverage=0.75),
        _make_idea("obsession1", 3.7, capital_class="seed", regulation_class="unregulated", obsession_leverage=0.8),
        _make_idea("obsession2", 3.6, capital_class="bootstrappable", regulation_class="low", obsession_leverage=0.75),
        _make_idea("quick1", 3.5, capital_class="bootstrappable", time_to_revenue=3),
        _make_idea("quick2", 3.4, capital_class="bootstrappable", time_to_revenue=4),
        _make_idea("filler1", 3.3, capital_class="seed"),
        _make_idea("filler2", 3.2, capital_class="seed"),
        _make_idea("killer", 5.5, capital_class="vc_dependent", regulation_class="high", killer_flag=True),
    ]


def test_portfolio_size():
    pool = _make_diverse_pool()
    result = select_portfolio_from_ideas(pool, size=10)
    assert len(result.selected) == 10


def test_killer_excluded_by_default():
    pool = _make_diverse_pool()
    result = select_portfolio_from_ideas(pool, size=10, include_killers=False)
    ids = [i.id for i in result.selected]
    assert "killer" not in ids


def test_killer_included_when_flag():
    pool = _make_diverse_pool()
    result = select_portfolio_from_ideas(pool, size=10, include_killers=True)
    ids = [i.id for i in result.selected]
    assert "killer" in ids


def test_min_bootstrappable():
    pool = _make_diverse_pool()
    result = select_portfolio_from_ideas(pool, size=10)
    boot_count = sum(1 for i in result.selected if i.capital_class == "bootstrappable")
    assert boot_count >= 2


def test_max_vc_dependent():
    pool = _make_diverse_pool()
    result = select_portfolio_from_ideas(pool, size=10)
    vc_count = sum(1 for i in result.selected if i.capital_class == "vc_dependent")
    assert vc_count <= 3


def test_max_high_regulation():
    pool = _make_diverse_pool()
    result = select_portfolio_from_ideas(pool, size=10)
    high_reg_count = sum(1 for i in result.selected if i.regulation_class == "high")
    assert high_reg_count <= 2


def test_rationale_provided():
    pool = _make_diverse_pool()
    result = select_portfolio_from_ideas(pool, size=10)
    for idea in result.selected:
        assert idea.id in result.rationale
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/homo/idea-pipeline
python -m pytest tests/test_portfolio.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'select_portfolio_from_ideas'`

- [ ] **Step 3: Write portfolio.py**

Create `src/idea_pipeline/portfolio.py`:

```python
"""Portfolio selection: greedy + constraint backfill.

Selects a diverse set of N ideas from the vault that satisfies hard constraints
(min bootstrappable, max vc, max high-reg, cross-domain balance).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from idea_pipeline.schemas import IdeeNote
from idea_pipeline.vault_io import list_notes


@dataclass
class PortfolioConstraints:
    size: int = 10
    min_bootstrappable: int = 2
    max_vc_dependent: int = 3
    max_high_regulation: int = 2
    min_cross_domain: int = 3       # soft-hard: use as many as exist
    min_high_mastery: int = 2       # mastery_leverage >= threshold
    min_high_obsession: int = 2     # obsession_leverage >= threshold
    mastery_threshold: float = 0.6
    obsession_threshold: float = 0.6
    min_quick_win: int = 2          # time_to_first_revenue_months < 6
    include_killers: bool = False


@dataclass
class PortfolioResult:
    selected: list[IdeeNote]
    rationale: dict[str, str]       # idea_id → reason
    rejected_for_constraints: list[str]
    top_25_ranked: list[tuple[str, float]]


def select_portfolio_from_ideas(
    all_ideas: list[IdeeNote],
    size: int = 10,
    include_killers: bool = False,
) -> PortfolioResult:
    """Select a diverse portfolio of `size` ideas using greedy + backfill."""
    constraints = PortfolioConstraints(size=size, include_killers=include_killers)

    # Filter out killers unless requested
    candidates = [
        i for i in all_ideas
        if (include_killers or not i.killer_flag) and i.score is not None
    ]
    candidates.sort(key=lambda i: i.score or 0, reverse=True)

    top_25 = [(i.id, i.score or 0) for i in candidates[:25]]

    # Track constraint counters
    counts = {
        "bootstrappable": 0,
        "vc_dependent": 0,
        "high_regulation": 0,
        "cross_domain": 0,
        "high_mastery": 0,
        "high_obsession": 0,
        "quick_win": 0,
    }

    def can_add(idea: IdeeNote, current_size: int) -> bool:
        """Check hard maximum constraints."""
        remaining_slots = size - current_size
        if remaining_slots <= 0:
            return False
        if idea.capital_class == "vc_dependent" and counts["vc_dependent"] >= constraints.max_vc_dependent:
            return False
        if idea.regulation_class == "high" and counts["high_regulation"] >= constraints.max_high_regulation:
            return False
        return True

    def add_idea(idea: IdeeNote, reason: str) -> None:
        selected.append(idea)
        rationale[idea.id] = reason
        if idea.capital_class == "bootstrappable":
            counts["bootstrappable"] += 1
        if idea.capital_class == "vc_dependent":
            counts["vc_dependent"] += 1
        if idea.regulation_class == "high":
            counts["high_regulation"] += 1
        if idea.cross_domain_flag:
            counts["cross_domain"] += 1
        if idea.mastery_leverage >= constraints.mastery_threshold:
            counts["high_mastery"] += 1
        if idea.obsession_leverage >= constraints.obsession_threshold:
            counts["high_obsession"] += 1
        if idea.fit_time_to_first_revenue_months is not None and idea.fit_time_to_first_revenue_months < 6:
            counts["quick_win"] += 1

    selected: list[IdeeNote] = []
    rationale: dict[str, str] = {}
    rejected: list[str] = []
    selected_ids: set[str] = set()

    # Phase A: Greedy pass
    for idea in candidates:
        if len(selected) >= size:
            break
        if idea.id in selected_ids:
            continue
        if can_add(idea, len(selected)):
            add_idea(idea, f"Score #{candidates.index(idea) + 1} ({idea.score:.3f})")
            selected_ids.add(idea.id)
        else:
            rejected.append(idea.id)

    # Phase B: Must-have backfill
    # Check each minimum constraint; if not met, swap lowest-score non-critical idea
    min_checks = [
        ("bootstrappable", counts["bootstrappable"], constraints.min_bootstrappable,
         lambda i: i.capital_class == "bootstrappable", "bootstrappable capital"),
        ("cross_domain", counts["cross_domain"], constraints.min_cross_domain,
         lambda i: i.cross_domain_flag, "cross-domain knowledge"),
        ("high_mastery", counts["high_mastery"], constraints.min_high_mastery,
         lambda i: i.mastery_leverage >= constraints.mastery_threshold, "high mastery leverage"),
        ("high_obsession", counts["high_obsession"], constraints.min_high_obsession,
         lambda i: i.obsession_leverage >= constraints.obsession_threshold, "high obsession leverage"),
    ]

    for key, current, minimum, predicate, label in min_checks:
        needed = minimum - current
        if needed <= 0:
            continue

        # Find candidates that satisfy this constraint but aren't selected yet
        backfill_candidates = [
            i for i in candidates
            if i.id not in selected_ids and predicate(i)
        ]

        for backfill_idea in backfill_candidates[:needed]:
            if len(selected) < size:
                # Just add if space
                add_idea(backfill_idea, f"Backfill: {label}")
                selected_ids.add(backfill_idea.id)
            else:
                # Swap: remove lowest-score idea that doesn't serve a critical constraint role
                swappable = [
                    s for s in selected
                    if not predicate(s)  # doesn't help this constraint
                    and s.capital_class != "bootstrappable"  # don't remove bootstrappable if needed
                ]
                if swappable:
                    worst = min(swappable, key=lambda i: i.score or 0)
                    selected.remove(worst)
                    del rationale[worst.id]
                    selected_ids.discard(worst.id)
                    rejected.append(worst.id)
                    add_idea(backfill_idea, f"Backfill (swap): {label}")
                    selected_ids.add(backfill_idea.id)

    return PortfolioResult(
        selected=selected,
        rationale=rationale,
        rejected_for_constraints=rejected,
        top_25_ranked=top_25,
    )


def select_portfolio(
    vault_path: Path,
    size: int = 10,
    include_killers: bool = False,
) -> PortfolioResult:
    """Load ideas from vault and select portfolio."""
    all_idea_notes = list_notes(vault_path, IdeeNote).notes
    all_ideas = [n.model for n in all_idea_notes]
    return select_portfolio_from_ideas(all_ideas, size=size, include_killers=include_killers)
```

- [ ] **Step 4: Run tests**

```bash
cd /home/homo/idea-pipeline
python -m pytest tests/test_portfolio.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Wire up CLI command**

Add to `src/idea_pipeline/cli.py`:

```python
from idea_pipeline.portfolio import PortfolioResult, select_portfolio
```

Add command:

```python
@app.command("portfolio")
def portfolio_cmd(
    vault: Optional[Path] = _vault_option,
    size: int = typer.Option(10, "--size", help="Portfolio size (default 10)"),
    include_killers: bool = typer.Option(False, "--include-killers", help="Include killer-flag ideas"),
    out: Optional[Path] = typer.Option(None, "--out", help="Output path (default: portfolio/topN_DATE.md)"),
    ids: Optional[str] = typer.Option(None, "--ids", help="Comma-separated IDs for final-5 fixation"),
    from_file: Optional[Path] = typer.Option(None, "--from", help="Reference top10 portfolio file"),
) -> None:
    """Select a diverse portfolio of N ideas with constraint-based diversity."""
    import datetime
    from pathlib import Path

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    today = datetime.date.today().isoformat()
    portfolio_dir = Path(__file__).resolve().parent.parent.parent / "portfolio"
    portfolio_dir.mkdir(exist_ok=True)

    if ids:
        # Final-5 fixation mode: user provides exact IDs
        from idea_pipeline.vault_io import list_notes
        from idea_pipeline.schemas import IdeeNote

        selected_ids = [i.strip() for i in ids.split(",")]
        all_ideas = {n.model.id: n.model for n in list_notes(vault_path, IdeeNote).notes}
        selected = [all_ideas[i] for i in selected_ids if i in all_ideas]
        missing = [i for i in selected_ids if i not in all_ideas]
        if missing:
            console.print(f"[yellow]Warning: IDs not found:[/yellow] {missing}")

        out_path = out or portfolio_dir / f"final{size}_{today}.md"
        lines = [
            f"# Final {size} Portfolio — {today}",
            "",
            "User-selected from top portfolio. IDs are final.",
            "",
            "| # | Idea | Score |",
            "|---|------|------:|",
        ]
        for i, idea in enumerate(selected, 1):
            lines.append(f"| {i} | {idea.id} | {idea.score:.3f} |")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        console.print(f"[green]✓[/green] {out_path}")
        return

    result = select_portfolio(vault_path, size=size, include_killers=include_killers)

    out_path = out or portfolio_dir / f"top{size}_{today}.md"

    lines = [
        f"# Portfolio Top {size} — {today}",
        "",
        f"**{size} ideas selected** by score + diversity constraints",
        "",
        "## Executive Summary",
        "",
    ]
    for i, idea in enumerate(result.selected, 1):
        desc = (idea.description or "")[:120]
        lines.append(f"{i}. **{idea.id}** — {desc}")

    lines += [
        "",
        "## Details",
        "",
        f"| # | Idea | Score | Cap | Reg | Mast | Obs | CD | Kill | Reason |",
        f"|---|------|------:|:---:|:---:|:----:|:---:|:--:|:----:|--------|",
    ]
    for i, idea in enumerate(result.selected, 1):
        cd = "✓" if idea.cross_domain_flag else "·"
        kill = "💀" if idea.killer_flag else "·"
        lines.append(
            f"| {i} | {idea.id} | {idea.score:.3f} "
            f"| {(idea.capital_class or '—')[:4]} "
            f"| {(idea.regulation_class or '—')[:4]} "
            f"| {idea.mastery_leverage:.2f} "
            f"| {idea.obsession_leverage:.2f} "
            f"| {cd} | {kill} "
            f"| {result.rationale.get(idea.id, '—')} |"
        )

    lines += [
        "",
        "## Top 25 Ranked (reference)",
        "",
        "| # | Idea | Score | In Portfolio? |",
        "|---|------|------:|:-------------:|",
    ]
    selected_ids = {i.id for i in result.selected}
    for rank, (iid, score) in enumerate(result.top_25_ranked, 1):
        in_p = "✓" if iid in selected_ids else "—"
        lines.append(f"| {rank} | {iid} | {score:.3f} | {in_p} |")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]✓[/green] Portfolio written: {out_path}")
    for i, idea in enumerate(result.selected, 1):
        console.print(f"  {i}. {idea.id} ({idea.score:.3f})")
```

- [ ] **Step 6: Test portfolio command**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe portfolio --size 10 2>&1 | head -20
```

Expected: 10 ideas selected, portfolio file written.

- [ ] **Step 7: Commit**

```bash
cd /home/homo/idea-pipeline
git add src/idea_pipeline/portfolio.py tests/test_portfolio.py src/idea_pipeline/cli.py
git commit -m "feat: portfolio top-10 selection with diversity constraints"
```

---

## Task 11: T5 Deep Research + Interview Briefs

**Files:**
- Modify: `src/idea_pipeline/cli.py` (brief command)

- [ ] **Step 1: Add brief command to cli.py**

```python
@app.command("brief")
def brief_cmd(
    vault: Optional[Path] = _vault_option,
    idea_id: Optional[str] = typer.Option(None, "--id", help="Generate brief for one idea"),
    all_final5: bool = typer.Option(False, "--all-final5", help="Generate briefs for all final-5 ideas"),
    final5_file: Optional[Path] = typer.Option(None, "--final5-file", help="Path to final5_*.md file"),
) -> None:
    """Generate expert-interview preparation briefs for final portfolio ideas.

    Requires T5 research to be complete for the idea(s).
    Produces Mom-Test-konforme Interviewfragen + competitive analysis.
    """
    import datetime
    from idea_pipeline.vault_io import list_notes, read_note
    from idea_pipeline.schemas import IdeeNote
    import anthropic

    vault_path = get_vault_path(vault)
    today = datetime.date.today().isoformat()

    portfolio_dir = Path(__file__).resolve().parent.parent.parent / "portfolio"
    briefs_dir = portfolio_dir / "final5_briefs"
    briefs_dir.mkdir(parents=True, exist_ok=True)

    # Determine which ideas to brief
    if idea_id:
        target_ids = [idea_id]
    elif all_final5 and final5_file:
        import re
        text = final5_file.read_text(encoding="utf-8")
        # Extract IDs from markdown table rows
        target_ids = re.findall(r"\|\s*\d+\s*\|\s*(\S+)\s*\|", text)
    elif idea_id is None and not all_final5:
        console.print("Specify --id IDEA_ID or --all-final5 --final5-file FILE")
        raise typer.Exit(1)
    else:
        target_ids = []

    all_ideas = {n.model.id: n.model for n in list_notes(vault_path, IdeeNote).notes}

    client = anthropic.Anthropic()

    for tid in target_ids:
        idea = all_ideas.get(tid)
        if not idea:
            console.print(f"[red]✗ Not found:[/red] {tid}")
            continue

        console.print(f"[dim]Generating brief for {tid}...[/dim]")

        prompt = f"""Du bereitest ein Experten-Interview für eine Geschäftsidee vor.

IDEE: {idea.id}
BESCHREIBUNG: {idea.description or "(keine)"}
RESEARCH NOTES: {idea.research_notes or "(keine T5-Daten vorhanden)"}

Erstelle einen strukturierten Brief auf Deutsch mit:

1. **Zusammenfassung** (1 Absatz, 3-4 Sätze)
2. **Top 3 Thesen zum Testen** (was muss wahr sein damit die Idee funktioniert?)
3. **Top 3 kritische Gegenargumente** (aus Research oder Logik)
4. **Vergleichbare Wettbewerber/Referenzen** (3 Stück, je 1 Satz)
5. **Zielpersonen** (wer wäre der ideale Gesprächspartner?)
6. **Mom-Test-konforme Fragen** (5-8 Stück)
   - Nur Vergangenheits- und Verhaltensfragen ("Wie haben Sie bisher...?", "Wann haben Sie zuletzt...?")
   - Keine Suggestivfragen ("Würden Sie X kaufen?")
   - Keine Zukunftsfragen ("Würden Sie das nutzen?")

Format: Markdown. Jeder Abschnitt als eigene Sektion mit ##-Überschrift."""

        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            )
            brief_text = response.content[0].text
            out_path = briefs_dir / f"{tid}_{today}.md"
            header = f"# Interview Brief: {tid} — {today}\n\n"
            out_path.write_text(header + brief_text, encoding="utf-8")
            console.print(f"[green]✓[/green] {out_path}")
        except Exception as e:
            console.print(f"[red]✗[/red] {tid}: {e}")
```

- [ ] **Step 2: Test brief dry-run structure**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe brief --help
```

Expected: help text shows --id, --all-final5, --final5-file options.

- [ ] **Step 3: Run T5 on final 5 (after user selects them)**

```bash
cd /home/homo/idea-pipeline
# First run T5 research (after user has run `portfolio --ids a,b,c,d,e`)
python -m ideapipe research --tier 5 --ids a,b,c,d,e
```

Note: The `--ids` filter for research also needs to be added (similar to `--top`). Check if it already exists; if not, add an `--ids` filter to the research command:

In `research_cmd`, add:

```python
filter_ids: Optional[str] = typer.Option(None, "--ids", help="Comma-separated idea IDs to research"),
```

And in the filtering loop:

```python
    filter_id_set = set(filter_ids.split(",")) if filter_ids else None
    # ...
    if filter_id_set is not None and idea.id not in filter_id_set:
        skipped += 1
        continue
```

- [ ] **Step 4: Commit**

```bash
cd /home/homo/idea-pipeline
git add src/idea_pipeline/cli.py
git commit -m "feat: brief command + research --ids filter for final-5 workflow"
```

---

## Task 12: Docs Update

**Files:**
- Modify: `CLAUDE.md` (if exists, else create minimal one)
- Modify: `README.md`

- [ ] **Step 1: Check if CLAUDE.md exists**

```bash
ls /home/homo/idea-pipeline/CLAUDE.md 2>/dev/null && echo exists || echo missing
```

- [ ] **Step 2: Update/create CLAUDE.md with v2.1 section**

Add or update the section in CLAUDE.md:

```markdown
## Scoring v2.1 (active)

Four dimensions:
- **market** (0.35): market_size, market_potential, willingness_to_pay, market_awareness
- **fit** (0.28): Mastery×Obsession knowledge model + difficulty + time_to_revenue
- **chance** (0.20): linked ChanceNote average (unchanged from v1)
- **attractiveness** (0.17): impact, innovativeness, mission_fit

Knowledge model (Mastery × Obsession):
- `mastery_leverage` (0.0–1.0): confidence×0.5 + credibility×0.3 + contacts×0.2
- `obsession_leverage` (0.0–1.0): enjoyment
- `cross_domain_flag`: true if any linked wissen has mastery≥0.65 AND any has obsession≥0.65
- No-links floor: 0.4 for both axes
- Computed deterministically from vault — never from LLM

Gates (LLM-populated, annotations only):
- `capital_class`: bootstrappable / seed / vc_dependent
- `regulation_class`: unregulated / low / high
- `killer_flag`: vc_dependent AND high → excluded from portfolio

v1 preserved in `scoring_v1.py`. Use `ideapipe score --version v1` for legacy.

## Pipeline Flow (v2.1)

```
enrich-intrinsic   # LLM rebuilds attractiveness/fit/gates (~$5)
score              # v2.1 scoring on full vault
report --version v2.1 --out LEADERBOARD_V2_1.md
research --tier 3 --top 25
research --tier 4 --top 25
score --trigger t3_t4_research
report --version v2.1 --out LEADERBOARD_V2_1_post_t3t4.md
portfolio --size 10
# USER: picks 5 from top10
portfolio --size 5 --ids a,b,c,d,e
research --tier 5 --ids a,b,c,d,e
brief --all-final5 --final5-file portfolio/final5_DATE.md
```
```

- [ ] **Step 3: Commit**

```bash
cd /home/homo/idea-pipeline
git add CLAUDE.md README.md
git commit -m "docs: v2.1 architecture, scoring model, pipeline flow"
```

---

## Self-Review

### Spec Coverage Check

| Spec Requirement | Covered in Task |
|-----------------|-----------------|
| Bug 1: double market scores | Task 3 (market_score uses only market fields) |
| Bug 2: wissen=0 punishment | Task 3 (floor at 0.4) |
| Bug 3: intrinsic mixes objectives | Task 3 (split into attractiveness + fit) |
| Bug 4: top 4 manually optimized | Task 7 (LLM rebuild replaces manual values) |
| Bug 5: linear scale | Task 3 (inv_log mapping) |
| Bug 6: T5 scoring-dead | Task 3 (t5_risk_flag in schema, used as annotation) |
| Bug 7: missing killer criteria | Task 1+3+6 (capital/regulation gates, killer_flag) |
| Bug 8: one-dimensional knowledge | Task 3 (mastery×obsession two axes) |
| Log-scale | Task 3 (inv_log) |
| score_history append-only | Task 3 (append_to_history) |
| Mastery×Obsession model | Task 3 (compute_mastery, compute_obsession) |
| Cross-domain flag + bonus | Task 3 |
| LLM intrinsic rebuild (Step 10) | Task 6+7 |
| Re-score v2.1 | Task 4+8 |
| LEADERBOARD_V2_1.md | Task 8 |
| compare-versions command | Task 8 |
| progression command | Task 8 |
| T3+T4 on Top 25 | Task 9 |
| LEADERBOARD_V2_1_post_t3t4.md | Task 9 |
| Portfolio top-10 constraints | Task 10 |
| Final-5 fixation | Task 10 |
| T5 on final 5 | Task 11 |
| Interview briefs (Mom-Test) | Task 11 |
| score_v1 baseline freeze | Task 5 |
| CLAUDE.md docs | Task 12 |

### Placeholder Scan

All code blocks are complete. No TBDs.

### Type Consistency

- `ScoreHistoryEntry` defined in Task 1, used in Task 3 and Task 4 ✓
- `compute_mastery` / `compute_obsession` defined in Task 3, tested in Task 3 ✓
- `PortfolioResult.selected` is `list[IdeeNote]`, CLI iterates `idea.score` (field exists) ✓
- `enrich_intrinsic_cmd` imports `run_intrinsic_enrich` from `enrich_intrinsic` ✓

---

**Plan saved to `docs/superpowers/plans/2026-04-19-scoring-refactor-v21.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
