# Step 8: T0 Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `ideapipe score` — a purely deterministic command that scores all ideas from vault data alone and outputs a leaderboard.

**Architecture:** `scoring.py` loads `weights.yaml`, reads all IdeeNotes + linked ChanceNotes + WissenNotes, computes a weighted additive score (all values inverted: 7−x so higher=better), writes `score`, `score_breakdown`, `score_version`, `scored_at` back into each IdeeNote atomically, then prints a Rich leaderboard. No LLM, no network.

**Tech Stack:** Python 3.11, PyYAML, Pydantic v2, Rich (already used in cli.py), existing `vault_io.list_notes` / `write_note`.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `src/idea_pipeline/scoring.py` | `score_vault()` + `ScoreResult` dataclass |
| Modify | `src/idea_pipeline/cli.py` | Add `score` command after `link_cmd` |

---

### Task 1: Implement `scoring.py`

**Files:**
- Modify: `src/idea_pipeline/scoring.py`

- [ ] **Step 1: Replace scoring.py with full implementation**

```python
"""Scoring engine: additive, weighted T0 scoring (vault-only, no LLM).

Formula (v1, weights from config/weights.yaml):
    idea_total = 0.40 * chance_avg + 0.25 * wissen_avg + 0.35 * intrinsic_avg

All raw values inverted (7 - x) so higher score = better idea.
Missing links → contribution from that category is 0 (not penalised further).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from idea_pipeline.schemas import ChanceNote, IdeeNote, WissenNote
from idea_pipeline.vault_io import VaultNote, list_notes, write_note

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WEIGHTS_PATH = _PROJECT_ROOT / "config" / "weights.yaml"


# ---------------------------------------------------------------------------
# Weight loading
# ---------------------------------------------------------------------------

def _load_weights() -> dict:
    return yaml.safe_load(_WEIGHTS_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Score helpers
# ---------------------------------------------------------------------------

def _inv(v: int) -> float:
    """Invert a 1-6 score so higher = better."""
    return 7.0 - v


def _weighted_avg(values: dict[str, int], weights: dict[str, float]) -> float:
    """Compute weighted average over fields present in both dicts."""
    total_w = 0.0
    total_v = 0.0
    for key, w in weights.items():
        v = values.get(key)
        if v is not None:
            total_v += _inv(v) * w
            total_w += w
    if total_w == 0:
        return 0.0
    return total_v / total_w


def _score_chance(chance: ChanceNote, weights: dict) -> float:
    values = {
        "granularitaet": chance.granularitaet,
        "urgency": chance.urgency,
        "prevalence": chance.prevalence,
        "impact": chance.impact,
        "personal_experience": chance.personal_experience,
        "market_awareness": chance.market_awareness,
    }
    return _weighted_avg(values, weights["chance"])


def _score_wissen(wissen: WissenNote, weights: dict) -> float:
    values = {
        "enjoyment": wissen.enjoyment,
        "confidence": wissen.confidence,
        "credebility": wissen.credibility,  # Python name; weights key is credebility
        "contacts": wissen.contacts,
    }
    return _weighted_avg(values, weights["wissen"])


def _score_intrinsic(idea: IdeeNote, weights: dict) -> float:
    values = {
        "market_size": idea.market_size,
        "market_potential": idea.market_potential,
        "impact": idea.impact,
        "difficulty": idea.difficulty,
        "time_investment": idea.time_investment,
        "innovativeness": idea.innovativeness,
    }
    return _weighted_avg(values, weights["idee_intrinsic"])


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ScoreResult:
    scored: list[tuple[str, float]] = field(default_factory=list)   # (idea_id, score) sorted desc
    skipped: list[str] = field(default_factory=list)                 # ideas with no description


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def score_vault(
    vault_path: Path,
    dry_run: bool = False,
    top_n: Optional[int] = None,
) -> ScoreResult:
    weights = _load_weights()
    tl = weights["top_level"]

    # Load all note types
    ideen = list_notes(vault_path, IdeeNote).notes
    chances_by_id: dict[str, ChanceNote] = {
        n.model.id: n.model for n in list_notes(vault_path, ChanceNote).notes
    }
    wissen_by_id: dict[str, WissenNote] = {
        n.model.id: n.model for n in list_notes(vault_path, WissenNote).notes
    }

    result = ScoreResult()
    today = date.today().isoformat()

    for vnote in ideen:
        idea = vnote.model

        # Chance contribution
        linked_chances = [chances_by_id[cid] for cid in idea.chancen if cid in chances_by_id]
        if linked_chances:
            chance_score = sum(_score_chance(c, weights) for c in linked_chances) / len(linked_chances)
        else:
            chance_score = 0.0

        # Wissen contribution
        linked_wissen = [wissen_by_id[wid] for wid in idea.wissen if wid in wissen_by_id]
        if linked_wissen:
            wissen_score = sum(_score_wissen(w, weights) for w in linked_wissen) / len(linked_wissen)
        else:
            wissen_score = 0.0

        # Intrinsic contribution
        intrinsic_score = _score_intrinsic(idea, weights)

        total = (
            tl["chance_contribution"] * chance_score
            + tl["wissen_contribution"] * wissen_score
            + tl["intrinsic_contribution"] * intrinsic_score
        )
        total = round(total, 4)

        breakdown = {
            "chance_score": round(chance_score, 4),
            "wissen_score": round(wissen_score, 4),
            "intrinsic_score": round(intrinsic_score, 4),
            "chance_n": len(linked_chances),
            "wissen_n": len(linked_wissen),
        }

        result.scored.append((idea.id, total))

        if not dry_run:
            idea.score = total
            idea.score_breakdown = breakdown
            idea.score_version = "v1"
            idea.scored_at = today
            write_note(vnote)

    result.scored.sort(key=lambda x: x[1], reverse=True)
    if top_n:
        result.scored = result.scored[:top_n]

    return result
```

- [ ] **Step 2: Verify import**

```bash
cd /home/homo/idea-pipeline && source .venv/bin/activate && python3 -c "from idea_pipeline.scoring import score_vault; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/idea_pipeline/scoring.py
git commit -m "step 8: implement scoring engine"
```

---

### Task 2: Add `score` command to CLI

**Files:**
- Modify: `src/idea_pipeline/cli.py`

- [ ] **Step 1: Add import** (with other imports at top of cli.py)

Add after `from idea_pipeline.link import LinkResult, run_link`:
```python
from idea_pipeline.scoring import ScoreResult, score_vault
```

- [ ] **Step 2: Add command** (after `link_cmd`, before `if __name__ == "__main__"`)

```python
@app.command("score")
def score_cmd(
    vault: Optional[Path] = _vault_option,
    tier: int = typer.Option(0, "--tier", "-t", help="Research tier (0=vault only)"),
    top: Optional[int] = typer.Option(None, "--top", "-n", help="Show only top N ideas"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Compute scores without writing to vault"),
) -> None:
    """Score all ideas and print a leaderboard (T0: vault-only, no research).

    Writes score, score_breakdown, score_version, scored_at into each idea note.
    Idempotent — safe to re-run after adding new chances or wissen links.
    """
    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    if tier != 0:
        console.print(f"[red]✗ Only --tier 0 is implemented (got {tier})[/red]")
        raise typer.Exit(1)

    if dry_run:
        console.print("[bold yellow]Dry run[/bold yellow] — scores computed but not written.\n")

    console.print(f"Scoring [cyan]{vault_path}[/cyan] (T0) ...\n")

    try:
        result = score_vault(vault_path, dry_run=dry_run, top_n=top)
    except Exception as e:
        console.print(f"[red]✗ Scoring failed:[/red] {e}")
        raise typer.Exit(1)

    from rich.table import Table
    table = Table(title=f"Leaderboard — T0{'  (dry run)' if dry_run else ''}", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Idea", style="cyan", no_wrap=False, max_width=55)
    table.add_column("Score", justify="right", style="bold green")

    for rank, (idea_id, score) in enumerate(result.scored, 1):
        table.add_row(str(rank), idea_id, f"{score:.3f}")

    console.print(table)
    console.print(f"\n[dim]{len(result.scored)} ideas scored[/dim]")
```

- [ ] **Step 3: Test dry-run**

```bash
source .venv/bin/activate && ideapipe score --dry-run 2>&1 | head -20
```

Expected: leaderboard table printed, no files written.

- [ ] **Step 4: Run for real**

```bash
source .venv/bin/activate && ideapipe score --top 20 2>&1
```

Expected: top-20 leaderboard, scores between 0 and 6.

- [ ] **Step 5: Verify a scored idea note**

```bash
source .venv/bin/activate && ideapipe vault read ~/vaults/idea-validation/$(ideapipe score --top 1 2>&1 | grep -oP '(?<=│ \d  │ )\S+') -v 2>&1 | head -20
```

Expected: note shows `score:`, `score_breakdown:`, `score_version: v1`, `scored_at:` fields.

- [ ] **Step 6: Commit and push**

```bash
git add src/idea_pipeline/cli.py docs/
git commit -m "step 8: add ideapipe score command + leaderboard"
git push
```
