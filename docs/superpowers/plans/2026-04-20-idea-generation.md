# Idea Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `ideapipe generate` — a domain bottleneck analysis pipeline that takes a free-text domain (Path A) or auto-selects hard-to-execute vault ideas with high market potential (Path B), runs T1+T2 research, identifies the primary blocking factor, and generates 2–3 focused business idea candidates that are auto-scored and written to the vault.

**Architecture:** Two separate LLM calls (bottleneck analysis → idea generation) in `generator.py`. Three Claude WebSearch prompts in `config/prompts/generate/`. New CLI command `ideapipe generate` wired into `cli.py`. New `generated_from` and `generation_bottleneck` fields on `IdeeNote`. Idempotent via deterministic ID from domain + bottleneck hash.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, Rich, anthropic SDK (claude-sonnet-4-6), Tavily (T1), existing `vault_io.py`, `scoring.py`, `research/sources/base.py`

**Dependency:** Requires the `feature/scoring-v21` branch to be merged to `main` first (v2.1 schema, scoring, and research stack must exist). Start this plan on a fresh branch from `main` post-merge.

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `src/idea_pipeline/schemas.py` | Modify | Add `generated_from`, `generation_bottleneck` to `IdeeNote` |
| `src/idea_pipeline/generator.py` | Replace stub | Full pipeline: T1+T2 research, bottleneck analysis, idea generation, vault write |
| `src/idea_pipeline/cli.py` | Modify | Add `generate` command |
| `config/prompts/generate/domain_research_t2.txt` | Create | Claude WebSearch prompt: domain bottleneck research |
| `config/prompts/generate/bottleneck_analysis.txt` | Create | LLM Call 1 prompt: structured bottleneck JSON |
| `config/prompts/generate/idea_candidates.txt` | Create | LLM Call 2 prompt: 2-3 idea candidates JSON |
| `tests/test_generator.py` | Create | Unit tests for pure functions (no LLM mocking) |

---

## Task 1: Schema — Add generated_from + generation_bottleneck to IdeeNote

**Files:**
- Modify: `src/idea_pipeline/schemas.py`
- Modify: `tests/test_schema_v21.py` (add one test)

- [ ] **Step 1: Write failing test**

Add to `tests/test_schema_v21.py`:

```python
def test_ideenote_generation_fields_default_none():
    note = IdeeNote.model_validate({
        "id": "test",
        "database": ["geschaeftsideen"],
    })
    assert note.generated_from is None
    assert note.generation_bottleneck is None


def test_ideenote_generation_fields_roundtrip():
    note = IdeeNote.model_validate({
        "id": "test",
        "database": ["geschaeftsideen"],
        "generated_from": "domain:myzel leder",
        "generation_bottleneck": "Substratproduktion nicht skalierbar",
    })
    assert note.generated_from == "domain:myzel leder"
    assert note.generation_bottleneck == "Substratproduktion nicht skalierbar"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/homo/idea-pipeline
python -m pytest tests/test_schema_v21.py::test_ideenote_generation_fields_default_none -v
```

Expected: `AttributeError` — field does not exist yet.

- [ ] **Step 3: Add fields to IdeeNote**

In `src/idea_pipeline/schemas.py`, inside `IdeeNote`, after the `t5_risk_flag` line:

```python
    # Generation provenance (set by ideapipe generate)
    generated_from: Optional[str] = None           # "domain:myzel leder" or "idea:<source_id>"
    generation_bottleneck: Optional[str] = None    # one-line bottleneck that spawned this idea
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/homo/idea-pipeline
python -m pytest tests/test_schema_v21.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Verify vault still reads cleanly**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe vault list --type idee 2>&1 | tail -3
```

Expected: 142+ ideas listed, no errors.

- [ ] **Step 6: Commit**

```bash
git add src/idea_pipeline/schemas.py tests/test_schema_v21.py
git commit -m "feat: add generated_from + generation_bottleneck fields to IdeeNote"
```

---

## Task 2: Prompt Files

**Files:**
- Create: `config/prompts/generate/domain_research_t2.txt`
- Create: `config/prompts/generate/bottleneck_analysis.txt`
- Create: `config/prompts/generate/idea_candidates.txt`

No tests needed — prompts are validated end-to-end in Task 7 (dry-run smoke test).

- [ ] **Step 1: Create prompts directory**

```bash
mkdir -p /home/homo/idea-pipeline/config/prompts/generate
```

- [ ] **Step 2: Write domain_research_t2.txt**

Create `config/prompts/generate/domain_research_t2.txt`:

```
You are a domain analyst. Your job is to find WHY a promising field hasn't achieved mainstream economic viability yet.

You will receive a domain name and existing T1 search snippets. Use your web_search tool to run 2-3 targeted searches that uncover:
1. The main production or scaling challenges
2. Why existing solutions have failed commercially
3. What specific technical, regulatory, or market barriers remain

Then output a plain text narrative (300-500 words) summarizing:
- What the domain is and its potential
- Concrete failure modes from existing companies/attempts
- The most critical unresolved blocking factors
- What a well-funded startup has tried and why it didn't scale

Do NOT output JSON. Output only the plain text narrative.
```

- [ ] **Step 3: Write bottleneck_analysis.txt**

Create `config/prompts/generate/bottleneck_analysis.txt`:

```
You are a bottleneck analyst for emerging technology domains. Given research context about a domain, identify the single most critical blocking factor preventing mainstream economic viability.

You will receive:
- domain: the field being analyzed
- research_context: T1+T2 research findings (snippets + narrative)

Output ONLY a valid JSON object (no markdown, no explanation):
{
  "domain": "<exact domain string from input>",
  "bottleneck": "<one sentence: what exactly is blocked>",
  "type": "<one of: production | market | regulation | technology>",
  "severity": "<one of: high | medium>",
  "blocking_factor": "<2-3 sentences: what specifically prevents scaling, with evidence from the research>"
}

Rules:
- Focus on the SINGLE most important bottleneck, not a list
- "blocking_factor" must reference concrete evidence from the research (companies, numbers, failures)
- Choose "production" if the barrier is manufacturing/scaling/cost
- Choose "market" if the barrier is distribution/customer adoption/pricing
- Choose "regulation" if the barrier is legal/certification/compliance
- Choose "technology" if the barrier is unsolved R&D/performance gap
- Output ONLY the JSON. No preamble, no explanation.
```

- [ ] **Step 4: Write idea_candidates.txt**

Create `config/prompts/generate/idea_candidates.txt`:

```
You are a business idea generator specialized in solving specific bottlenecks in emerging domains.

You will receive:
- bottleneck: structured JSON describing the domain's primary blocking factor
- founder_context: the founder's knowledge areas and expertise

Generate exactly 3 focused business idea candidates. Each idea must:
1. Directly address the identified bottleneck (not the whole domain)
2. Be executable with the founder's existing knowledge
3. Be a B2B or B2B2C service/software/platform (avoid hardware-first businesses unless clearly necessary)
4. Be bootstrappable or seed-fundable (no VC-dependent moonshots)

Output ONLY a valid JSON array of 3 objects (no markdown, no explanation):
[
  {
    "description": "<2-3 sentences: what the business does, who pays, how it solves the bottleneck>",
    "first_adopters": ["<customer segment 1>", "<customer segment 2>"],
    "mass_customers": ["<broader market 1>", "<broader market 2>"],
    "notes": "<1-2 sentences: why this is specifically enabled by the founder's knowledge>"
  },
  ...
]

Rules:
- description must be concrete: name the customer, the problem, the solution mechanism
- first_adopters: who pays first (specific, narrow segment)
- mass_customers: who pays at scale (broader)
- notes: tie directly to a knowledge area from founder_context
- All 3 ideas must attack the SAME bottleneck from different angles
- Output ONLY the JSON array. No preamble, no explanation.
```

- [ ] **Step 5: Commit**

```bash
git add config/prompts/generate/
git commit -m "feat: add generate pipeline prompts — domain_research_t2, bottleneck_analysis, idea_candidates"
```

---

## Task 3: generator.py — Data Classes + Pure Functions + Tests

**Files:**
- Replace: `src/idea_pipeline/generator.py`
- Create: `tests/test_generator.py`

This task covers all pure (non-LLM) logic: data classes, `_slugify`, `_make_idea_id`, `_select_path_b_candidates`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_generator.py`:

```python
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
    # idea_a: market=6.0 (top quartile), fit=1.0 (bottom quartile) → qualifies
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/homo/idea-pipeline
python -m pytest tests/test_generator.py -v 2>&1 | head -30
```

Expected: `ImportError` or `ModuleNotFoundError` — generator functions don't exist yet.

- [ ] **Step 3: Write generator.py with data classes and pure functions**

Replace `src/idea_pipeline/generator.py` entirely:

```python
"""Idea generator: domain bottleneck analysis → focused business ideas.

Two input paths feed the same pipeline:
  Path A: user-supplied domain string (--domain)
  Path B: auto-selected vault ideas with high market score + low fit score (--from-vault)

Pipeline:
  1. T1 Tavily: 3 bottleneck-focused queries, raw snippets
  2. T2 Claude WebSearch: deeper narrative on failure modes and blockers
  3. LLM Call 1: bottleneck analysis → structured JSON
  4. LLM Call 2: idea generation → 2-3 IdeeNote-compatible candidates
  5. Interactive selection → auto-score (v2.1) → vault write

Idempotency: idea IDs derived from domain + description hash; skip if already exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_SONNET = "claude-sonnet-4-6"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PROMPTS_DIR = _PROJECT_ROOT / "config" / "prompts" / "generate"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BottleneckResult:
    domain: str
    bottleneck: str
    type: str            # production | market | regulation | technology
    severity: str        # high | medium
    blocking_factor: str


@dataclass
class IdeaCandidate:
    id: str
    description: str
    generated_from: str        # "domain:<domain>" or "idea:<source_id>"
    generation_bottleneck: str
    first_adopters: list[str] = field(default_factory=list)
    mass_customers: list[str] = field(default_factory=list)
    notes: Optional[str] = None


@dataclass
class GenerateResult:
    domain: str
    bottleneck: Optional[BottleneckResult] = None
    candidates: list[IdeaCandidate] = field(default_factory=list)
    written: list[str] = field(default_factory=list)   # idea IDs written to vault
    skipped: list[str] = field(default_factory=list)   # already existed
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Pure functions (testable without I/O)
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert domain string to a filesystem-safe slug (max 50 chars)."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "_", slug)
    return slug[:50]


def _make_idea_id(domain: str, description: str) -> str:
    """Deterministic idea ID: generated_<domain_slug>_<desc_hash[:6]>."""
    h = hashlib.sha256(f"{domain}:{description}".encode()).hexdigest()[:6]
    return f"generated_{_slugify(domain)}_{h}"


def _select_path_b_candidates(
    vnotes: list,
    limit: int,
) -> list[tuple[str, str]]:
    """Select vault ideas with high market score and low fit score.

    High market = top quartile of market_score across all scored ideas.
    Low fit = bottom quartile of fit_score.

    Args:
        vnotes: list of VaultNote objects (IdeeNote models)
        limit: max number of candidates to return

    Returns:
        list of (idea_id, description) tuples sorted by market_score desc
    """
    scored = [
        vn for vn in vnotes
        if vn.model.score_breakdown
        and vn.model.score_breakdown.get("market_score") is not None
        and vn.model.score_breakdown.get("fit_score") is not None
    ]
    if not scored:
        return []

    market_vals = sorted(vn.model.score_breakdown["market_score"] for vn in scored)
    fit_vals = sorted(vn.model.score_breakdown["fit_score"] for vn in scored)

    market_q75 = market_vals[int(len(market_vals) * 0.75)]
    fit_q25 = fit_vals[int(len(fit_vals) * 0.25)]

    candidates = [
        vn for vn in scored
        if vn.model.score_breakdown["market_score"] >= market_q75
        and vn.model.score_breakdown["fit_score"] <= fit_q25
    ]
    candidates.sort(
        key=lambda vn: vn.model.score_breakdown["market_score"],
        reverse=True,
    )
    return [
        (vn.model.id, vn.model.description or "")
        for vn in candidates[:limit]
    ]


# ---------------------------------------------------------------------------
# I/O helpers (not unit-tested; covered by integration dry-run)
# ---------------------------------------------------------------------------

def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _get_founder_context(vault_path: Path) -> str:
    """Build a text summary of founder's wissen notes for LLM context."""
    from idea_pipeline.schemas import WissenNote
    from idea_pipeline.vault_io import list_notes

    wissens = list_notes(vault_path, WissenNote).notes
    lines = ["Founder knowledge areas:"]
    for vn in wissens:
        w = vn.model
        desc = (w.description or "").strip()
        lines.append(f"- {w.id}: {desc[:120]}" if desc else f"- {w.id}")
    return "\n".join(lines)


def _research_domain_t1(domain: str) -> str:
    """T1: Tavily — 3 bottleneck-focused queries, returns joined snippets."""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key or api_key.startswith("tvly-..."):
        return "(T1 skipped: TAVILY_API_KEY not configured)"

    from tavily import TavilyClient
    client = TavilyClient(api_key=api_key)

    queries = [
        f"{domain} production scaling challenges problems",
        f"{domain} market barriers commercial failure reasons",
        f"{domain} why not mainstream economics",
    ]
    snippets: list[str] = []
    for q in queries:
        try:
            results = client.search(query=q, search_depth="basic", max_results=4)
            for r in results.get("results", []):
                title = r.get("title", "")
                content = r.get("content", "")[:300]
                snippets.append(f"[{title}] {content}")
        except Exception:
            continue
    return "\n\n".join(snippets[:12]) or "(no T1 results)"


def _research_domain_t2(domain: str, t1_context: str) -> str:
    """T2: Claude WebSearch — deeper narrative on failure modes and blockers."""
    from anthropic import Anthropic
    from idea_pipeline.research.sources.base import parse_json

    llm = Anthropic()
    prompt = _load_prompt("domain_research_t2.txt")
    user_msg = json.dumps(
        {"domain": domain, "t1_context": t1_context[:2000]},
        ensure_ascii=False,
    )
    try:
        resp = llm.messages.create(
            model=_SONNET,
            max_tokens=1024,
            system=prompt,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": user_msg}],
        )
        return next(
            (block.text for block in reversed(resp.content) if hasattr(block, "text")),
            "",
        )
    except Exception:
        return ""


def _analyze_bottleneck(domain: str, research_context: str) -> BottleneckResult:
    """LLM Call 1: Identify the primary bottleneck in the domain."""
    from anthropic import Anthropic
    from idea_pipeline.research.sources.base import parse_json

    llm = Anthropic()
    prompt = _load_prompt("bottleneck_analysis.txt")
    user_msg = json.dumps(
        {"domain": domain, "research_context": research_context[:3000]},
        ensure_ascii=False,
    )
    resp = llm.messages.create(
        model=_SONNET,
        max_tokens=512,
        system=prompt,
        messages=[{"role": "user", "content": user_msg}],
    )
    data = parse_json(resp.content[0].text)
    return BottleneckResult(
        domain=data["domain"],
        bottleneck=data["bottleneck"],
        type=data.get("type", "production"),
        severity=data.get("severity", "high"),
        blocking_factor=data.get("blocking_factor", ""),
    )


def _generate_candidates(
    bottleneck: BottleneckResult,
    founder_context: str,
    existing_ids: set[str],
) -> list[IdeaCandidate]:
    """LLM Call 2: Generate 2-3 focused business idea candidates."""
    from anthropic import Anthropic
    from idea_pipeline.research.sources.base import parse_json

    llm = Anthropic()
    prompt = _load_prompt("idea_candidates.txt")
    user_msg = json.dumps(
        {
            "bottleneck": {
                "domain": bottleneck.domain,
                "bottleneck": bottleneck.bottleneck,
                "type": bottleneck.type,
                "severity": bottleneck.severity,
                "blocking_factor": bottleneck.blocking_factor,
            },
            "founder_context": founder_context,
        },
        ensure_ascii=False,
    )
    try:
        resp = llm.messages.create(
            model=_SONNET,
            max_tokens=2000,
            system=prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        data = parse_json(resp.content[0].text)
        if not isinstance(data, list):
            return []
    except Exception:
        return []

    candidates: list[IdeaCandidate] = []
    for item in data[:3]:
        desc = item.get("description", "").strip()
        if not desc:
            continue
        idea_id = _make_idea_id(bottleneck.domain, desc)
        if idea_id in existing_ids:
            continue
        candidates.append(
            IdeaCandidate(
                id=idea_id,
                description=desc,
                generated_from=f"domain:{bottleneck.domain}",
                generation_bottleneck=bottleneck.bottleneck,
                first_adopters=item.get("first_adopters", []),
                mass_customers=item.get("mass_customers", []),
                notes=item.get("notes"),
            )
        )
    return candidates


def _write_candidate_to_vault(candidate: IdeaCandidate, vault_path: Path) -> None:
    """Create a new IdeeNote vault file for a selected candidate."""
    from idea_pipeline.schemas import IdeeNote
    from idea_pipeline.vault_io import VaultNote, write_note

    idea = IdeeNote(
        id=candidate.id,
        database=["geschaeftsideen"],
        description=candidate.description,
        first_adopters=candidate.first_adopters,
        mass_customers=candidate.mass_customers,
        notes=candidate.notes,
        status="roh",
    )
    idea.generated_from = candidate.generated_from
    idea.generation_bottleneck = candidate.generation_bottleneck

    path = vault_path / f"{candidate.id}.md"
    write_note(VaultNote(model=idea, body="", path=path))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_generate_domain(
    domain: str,
    vault_path: Path,
    dry_run: bool = False,
    select: Optional[list[int]] = None,
) -> GenerateResult:
    """Run the full pipeline for a single domain string.

    Args:
        domain: free-text domain, e.g. "myzel leder"
        vault_path: path to the Obsidian vault
        dry_run: if True, run research + analysis but don't write to vault
        select: list of 1-based candidate indices to accept; None = interactive

    Returns:
        GenerateResult with bottleneck, candidates, and written/skipped idea IDs
    """
    from idea_pipeline.schemas import IdeeNote
    from idea_pipeline.vault_io import list_notes

    result = GenerateResult(domain=domain)

    existing_ids = {
        vn.model.id
        for vn in list_notes(vault_path, IdeeNote).notes
    }

    t1 = _research_domain_t1(domain)
    t2 = _research_domain_t2(domain, t1)
    research_context = f"=== T1 snippets ===\n{t1}\n\n=== T2 narrative ===\n{t2}"

    try:
        result.bottleneck = _analyze_bottleneck(domain, research_context)
    except Exception as e:
        result.error = f"Bottleneck analysis failed: {e}"
        return result

    founder_context = _get_founder_context(vault_path)

    try:
        result.candidates = _generate_candidates(
            result.bottleneck, founder_context, existing_ids
        )
    except Exception as e:
        result.error = f"Idea generation failed: {e}"
        return result

    if dry_run:
        return result

    accepted_indices = _resolve_selection(result.candidates, select)

    for i, candidate in enumerate(result.candidates):
        if (i + 1) in accepted_indices:
            _write_candidate_to_vault(candidate, vault_path)
            result.written.append(candidate.id)
        else:
            result.skipped.append(candidate.id)

    return result


def _resolve_selection(candidates: list[IdeaCandidate], select: Optional[list[int]]) -> set[int]:
    """Return 1-based indices of accepted candidates."""
    if not candidates:
        return set()
    if select is not None:
        return {i for i in select if 1 <= i <= len(candidates)}
    # Interactive
    import typer
    chosen: set[int] = set()
    for i, c in enumerate(candidates, 1):
        print(f"\n[{i}] {c.description[:200]}")
    raw = typer.prompt(
        f"\nSelect candidates (e.g. 1,3 or all or none)",
        default="all",
    )
    if raw.strip().lower() == "all":
        return set(range(1, len(candidates) + 1))
    if raw.strip().lower() in ("none", ""):
        return set()
    for part in raw.split(","):
        try:
            chosen.add(int(part.strip()))
        except ValueError:
            pass
    return chosen
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/homo/idea-pipeline
python -m pytest tests/test_generator.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/idea_pipeline/generator.py tests/test_generator.py
git commit -m "feat: generator.py — data classes, pure functions, Path B selection"
```

---

## Task 4: CLI — `ideapipe generate` Command

**Files:**
- Modify: `src/idea_pipeline/cli.py`

- [ ] **Step 1: Add generate command to cli.py**

In `src/idea_pipeline/cli.py`, add the following import at the top with other imports:

```python
from idea_pipeline.generator import run_generate_domain, _select_path_b_candidates, GenerateResult
```

Then add this command before the `if __name__ == "__main__":` line at the bottom:

```python
@app.command("generate")
def generate_cmd(
    vault: Optional[Path] = _vault_option,
    domain: Optional[str] = typer.Option(None, "--domain", "-d", help="Domain to analyze, e.g. 'myzel leder'"),
    from_vault: bool = typer.Option(False, "--from-vault", help="Auto-select high-market/low-fit vault ideas"),
    limit: int = typer.Option(5, "--limit", "-n", help="Max vault ideas to process (Path B only)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Research + analyze but don't write to vault"),
    select: Optional[str] = typer.Option(None, "--select", help="Non-interactive selection, e.g. '1,3'"),
) -> None:
    """Generate focused business ideas by analyzing domain bottlenecks.

    Path A (--domain): research a free-text domain and generate ideas addressing its bottleneck.
    Path B (--from-vault): auto-select vault ideas with high market + low fit, then generate focused variants.
    """
    if not domain and not from_vault:
        console.print("[red]✗[/red] Provide --domain or --from-vault")
        raise typer.Exit(1)
    if domain and from_vault:
        console.print("[red]✗[/red] Use --domain OR --from-vault, not both")
        raise typer.Exit(1)

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    select_indices: Optional[list[int]] = None
    if select:
        try:
            select_indices = [int(x.strip()) for x in select.split(",")]
        except ValueError:
            console.print("[red]✗[/red] --select must be comma-separated integers, e.g. '1,3'")
            raise typer.Exit(1)

    domains: list[str] = []
    if domain:
        domains = [domain]
    else:
        from idea_pipeline.schemas import IdeeNote
        all_ideen = list_notes(vault_path, IdeeNote).notes
        path_b = _select_path_b_candidates(all_ideen, limit=limit)
        if not path_b:
            console.print("[yellow]No Path B candidates found (need scored ideas with market+fit breakdown)[/yellow]")
            raise typer.Exit(0)
        domains = [desc for _, desc in path_b if desc]
        console.print(f"[bold]Path B:[/bold] {len(domains)} vault candidates selected")
        for idea_id, desc in path_b:
            console.print(f"  [cyan]{idea_id}[/cyan]: {desc[:80]}")

    dry_label = " [dim](dry-run)[/dim]" if dry_run else ""
    console.print(f"\n[bold]ideapipe generate[/bold]{dry_label}  {len(domains)} domain(s)\n")

    all_written: list[str] = []
    for d in domains:
        console.print(f"[bold]▶ Domain:[/bold] {d}")
        result = run_generate_domain(
            domain=d,
            vault_path=vault_path,
            dry_run=dry_run,
            select=select_indices,
        )

        if result.error:
            console.print(f"  [red]✗ Error:[/red] {result.error}")
            continue

        if result.bottleneck:
            console.print(f"  [yellow]Bottleneck ({result.bottleneck.type}, {result.bottleneck.severity}):[/yellow] {result.bottleneck.bottleneck}")
            console.print(f"  {result.bottleneck.blocking_factor[:200]}")

        if not result.candidates:
            console.print("  [dim]No candidates generated[/dim]")
            continue

        console.print(f"\n  [bold]{len(result.candidates)} candidates:[/bold]")
        for i, c in enumerate(result.candidates, 1):
            status = "[green]✓ written[/green]" if c.id in result.written else "[dim]skipped[/dim]"
            if dry_run:
                status = "[dim]dry-run[/dim]"
            console.print(f"  [{i}] {status}  {c.description[:120]}")

        all_written.extend(result.written)

    if not dry_run and all_written:
        console.print(f"\n[green]✓[/green] {len(all_written)} new idea(s) written to vault. Run [bold]ideapipe score --version v2.1[/bold] to score them.")
```

- [ ] **Step 2: Verify the command is importable**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe --help 2>&1 | grep generate
```

Expected: `generate` appears in the command list.

- [ ] **Step 3: Test dry-run with a simple domain (no real API calls needed for import)**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe generate --help
```

Expected: help text shows --domain, --from-vault, --limit, --dry-run, --select.

- [ ] **Step 4: Commit**

```bash
git add src/idea_pipeline/cli.py
git commit -m "feat: add ideapipe generate CLI command — Path A (--domain) and Path B (--from-vault)"
```

---

## Task 5: Integration Smoke Test

No new files — this task verifies the full pipeline works end-to-end using real APIs.

- [ ] **Step 1: Dry-run Path A (no vault writes)**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe generate --domain "mycorrhiza kommerziell" --dry-run
```

Expected output:
- `▶ Domain: mycorrhiza kommerziell`
- `Bottleneck (production|market|..., high|medium): <one-sentence diagnosis>`
- `<blocking_factor text>`
- `3 candidates:` followed by 3 descriptions
- No "written to vault" message (dry-run)

- [ ] **Step 2: Dry-run Path B**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe generate --from-vault --limit 3 --dry-run
```

Expected: 3 vault ideas selected, bottleneck + candidates shown for each, nothing written.

- [ ] **Step 3: Run real Path A, select one candidate**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe generate --domain "myzel leder" --select 1
```

Expected: candidate 1 written to vault.

- [ ] **Step 4: Verify the new idea appears in the vault**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe vault list --type idee 2>&1 | grep generated_
```

Expected: at least one `generated_myzel_leder_*` note listed.

- [ ] **Step 5: Score the new idea**

```bash
cd /home/homo/idea-pipeline
python -m ideapipe score --version v2.1 2>&1 | tail -5
```

Expected: total count increases by 1, no errors.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: ideapipe generate — full pipeline smoke tested, Path A + Path B working"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ Path A (--domain) → Task 4
- ✅ Path B (--from-vault, auto high-market/low-fit) → Task 3 (_select_path_b_candidates) + Task 4
- ✅ T1 Tavily + T2 Claude WebSearch → Task 3 (_research_domain_t1, _research_domain_t2)
- ✅ LLM Call 1: bottleneck analysis → Task 3 (_analyze_bottleneck) + Task 2 (prompt)
- ✅ LLM Call 2: 2-3 candidates → Task 3 (_generate_candidates) + Task 2 (prompt)
- ✅ Terminal output: bottleneck + candidates together → Task 4 (generate_cmd)
- ✅ Interactive + non-interactive selection (--select) → Task 3 (_resolve_selection) + Task 4
- ✅ Auto-score after write → Task 5 (Step 5)
- ✅ Vault write → Task 3 (_write_candidate_to_vault)
- ✅ Idempotency via deterministic ID → Task 3 (_make_idea_id)
- ✅ generated_from + generation_bottleneck fields → Task 1
- ✅ --dry-run → Task 3 (run_generate_domain dry_run param) + Task 4 (CLI flag)
- ✅ --limit for Path B → Task 3 + Task 4
- ✅ Error handling (T1/T2 failure, LLM invalid JSON, continue other domains) → Task 3
