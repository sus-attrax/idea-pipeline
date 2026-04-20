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
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
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

    Returns list of (idea_id, description) tuples sorted by market_score desc.
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
# I/O helpers
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


def _resolve_selection(candidates: list[IdeaCandidate], select: Optional[list[int]]) -> set[int]:
    """Return 1-based indices of accepted candidates."""
    if not candidates:
        return set()
    if select is not None:
        return {i for i in select if 1 <= i <= len(candidates)}
    import typer
    for i, c in enumerate(candidates, 1):
        print(f"\n[{i}] {c.description[:200]}")
    raw = typer.prompt(
        "\nSelect candidates (e.g. 1,3 or all or none)",
        default="all",
    )
    if raw.strip().lower() == "all":
        return set(range(1, len(candidates) + 1))
    if raw.strip().lower() in ("none", ""):
        return set()
    chosen: set[int] = set()
    for part in raw.split(","):
        try:
            chosen.add(int(part.strip()))
        except ValueError:
            pass
    return chosen


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_generate_domain(
    domain: str,
    vault_path: Path,
    dry_run: bool = False,
    select: Optional[list[int]] = None,
) -> GenerateResult:
    """Run the full bottleneck → generate pipeline for a single domain.

    Args:
        domain: free-text domain, e.g. "myzel leder"
        vault_path: path to the Obsidian vault
        dry_run: if True, research + analyze but don't write to vault
        select: 1-based indices of candidates to accept; None = interactive
    """
    from idea_pipeline.schemas import IdeeNote
    from idea_pipeline.vault_io import list_notes

    result = GenerateResult(domain=domain)

    existing_ids = {vn.model.id for vn in list_notes(vault_path, IdeeNote).notes}

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

    accepted = _resolve_selection(result.candidates, select)
    for i, candidate in enumerate(result.candidates):
        if (i + 1) in accepted:
            _write_candidate_to_vault(candidate, vault_path)
            result.written.append(candidate.id)
        else:
            result.skipped.append(candidate.id)

    return result
