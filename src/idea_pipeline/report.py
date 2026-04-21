"""Full-report generator for T4+ ideas.

Produces a detailed per-idea markdown report with score breakdown, all research
narratives (T2/T3/T4), linked problem fields, knowledge areas, and idea notes.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from idea_pipeline.research.cache import cache_get
from idea_pipeline.schemas import ChanceNote, IdeeNote, WissenNote
from idea_pipeline.vault_io import list_notes


# ---------------------------------------------------------------------------
# Narrative helpers
# ---------------------------------------------------------------------------

def _blockquote(text: str) -> str:
    """Prefix every line of *text* with '> ' so multi-paragraph narratives
    render as a proper markdown blockquote."""
    if not text:
        return ""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())


def _fetch_narrative(idea_id: str, tier_key: str) -> str:
    """Fetch cached research narrative for the given tier key.

    tier_key: "tier2", "tier3", or "tier4"
    Returns empty string if not available.
    """
    source_map = {
        "tier2": ("claude_search_v1", f"t2:{idea_id}"),
        "tier3": ("perplexity_v1", f"t3:{idea_id}"),
        "tier4": ("firecrawl_v2", f"t4:{idea_id}"),
    }
    if tier_key not in source_map:
        return ""
    source, query = source_map[tier_key]
    try:
        result = cache_get(query, source)
        if result and isinstance(result, dict):
            return result.get("narrative", "")
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Progress-bar helpers
# ---------------------------------------------------------------------------

def _bar(value: float) -> str:
    """Convert 0.0–1.0 float to a visual bar character."""
    if value >= 0.75:
        return "▇"
    if value >= 0.50:
        return "▅"
    if value >= 0.25:
        return "▃"
    return "▁"


# ---------------------------------------------------------------------------
# Per-idea section renderer
# ---------------------------------------------------------------------------

def _render_idea_section(
    rank: int,
    idea: IdeeNote,
    chance_notes_by_id: dict[str, ChanceNote],
    wissen_notes_by_id: dict[str, WissenNote],
    target_tier: int = 0,
) -> str:
    """Render a single idea's full-report section as markdown."""
    lines: list[str] = []

    sb = idea.score_breakdown or {}
    score = idea.score or 0.0
    fidelity = idea.research_fidelity or ""

    # Derive tier number from fidelity string
    tier_num = 0
    if fidelity.startswith("tier") and fidelity[4:].isdigit():
        tier_num = int(fidelity[4:])

    capital = idea.capital_class or "—"
    regulation = idea.regulation_class or "—"

    lines.append(f"## Rank #{rank}: {idea.id}")
    lines.append(
        f"**Score: {score:.3f}** | Tier: T{tier_num} | "
        f"Capital: {capital} | Regulation: {regulation}"
    )
    lines.append("")

    if target_tier > 0 and tier_num < target_tier:
        lines.append(
            f"> ⚠ **No additional T{target_tier} data obtained** — "
            f"selected as T{target_tier} candidate but research yielded no new results."
        )
        lines.append("")

    # --- Score Breakdown ---
    lines.append("### Score Breakdown")
    lines.append("| Dimension      | Score  | Weight | Contribution |")
    lines.append("|----------------|--------|--------|--------------|")

    weights = [("Market", "market_score", 0.35),
               ("Fit", "fit_score", 0.28),
               ("Chance", "chance_score", 0.20),
               ("Attractiveness", "attractiveness_score", 0.17)]
    for dim_label, dim_key, weight in weights:
        dim_score = sb.get(dim_key, 0.0)
        contribution = dim_score * weight
        lines.append(
            f"| {dim_label:<14} | {dim_score:.3f} | {weight*100:.0f}%    | {contribution:.3f}        |"
        )
    lines.append("")

    # --- Key Signals ---
    lines.append("### Key Signals")
    wtp = idea.willingness_to_pay
    mastery = idea.mastery_leverage
    obsession = idea.obsession_leverage
    mastery_pct = int(mastery * 100)
    obsession_pct = int(obsession * 100)
    cross = "✓" if idea.cross_domain_flag else "✗"
    killer = "✓ BLOCKED" if idea.killer_flag else "✗ clear"

    lines.append(f"- Willingness to Pay: {wtp}/6")
    lines.append(f"- Mastery Leverage: {_bar(mastery)} {mastery_pct}%")
    lines.append(f"- Obsession Leverage: {_bar(obsession)} {obsession_pct}%")
    lines.append(f"- Cross-Domain Bonus: {cross}")
    lines.append(f"- Killer Flag: {killer}")
    lines.append("")

    # --- Research Findings ---
    lines.append("### Research Findings")
    # Compute average prevalence from linked ChanceNotes (prevalence lives on ChanceNote, not IdeeNote)
    linked_chances = [chance_notes_by_id[cid] for cid in (idea.chancen or []) if cid in chance_notes_by_id]
    if linked_chances:
        avg_prev: str = f"{sum(c.prevalence for c in linked_chances) / len(linked_chances):.1f}"
    else:
        avg_prev = "—"
    lines.append(
        f"**T1:** Market size {idea.market_size}/6, "
        f"Potential {idea.market_potential}/6, "
        f"Awareness {idea.market_awareness}/6, "
        f"Prevalence (avg) {avg_prev}/6"
    )
    lines.append("")

    # T2
    if tier_num >= 2:
        n2 = _fetch_narrative(idea.id, "tier2")
        if n2:
            lines.append("**T2 (Claude + Web Search):**")
            lines.append(_blockquote(n2))
            lines.append("")

    # T3
    if tier_num >= 3:
        n3 = _fetch_narrative(idea.id, "tier3")
        if n3:
            lines.append("**T3 (Perplexity):**")
            lines.append(_blockquote(n3))
            lines.append("")

    # T4
    if tier_num >= 4:
        n4 = _fetch_narrative(idea.id, "tier4")
        if n4:
            lines.append("**T4 (Firecrawl):**")
            lines.append(_blockquote(n4))
            lines.append("")

    # T5 / manual research_notes
    if idea.research_notes:
        lines.append("**Research Notes (T5/manual):**")
        lines.append(_blockquote(idea.research_notes))
        lines.append("")

    # --- Linked Problem Fields ---
    lines.append("### Linked Problem Fields")
    if idea.chancen:
        for cid in idea.chancen:
            c = chance_notes_by_id.get(cid)
            if c:
                desc = (c.description or "").strip()
                desc_part = f" — {desc}" if desc else ""
                lines.append(
                    f"- **{cid}**{desc_part} | "
                    f"Urgency: {c.urgency}/6, "
                    f"Prevalence: {c.prevalence}/6, "
                    f"Impact: {c.impact}/6"
                )
            else:
                lines.append(f"- **{cid}** — (note not found)")
    else:
        lines.append("_No linked problem fields._")
    lines.append("")

    # --- Knowledge Areas ---
    lines.append("### Knowledge Areas")
    if idea.wissen:
        for wid in idea.wissen:
            w = wissen_notes_by_id.get(wid)
            if w:
                lines.append(
                    f"- **{wid}** — "
                    f"Confidence: {w.confidence}/6, "
                    f"Enjoyment: {w.enjoyment}/6, "
                    f"Credibility: {w.credibility}/6"
                )
            else:
                lines.append(f"- **{wid}** — (note not found)")
    else:
        lines.append("_No linked knowledge areas._")
    lines.append("")

    # --- Idea Notes ---
    if idea.notes:
        lines.append("### Idea Notes")
        lines.append(idea.notes)
        lines.append("")

    # --- Generation Context ---
    if idea.generated_from:
        lines.append("### Generation Context")
        lines.append(f"Generated from: {idea.generated_from}")
        if idea.generation_bottleneck:
            lines.append(f"Bottleneck: {idea.generation_bottleneck}")
        lines.append("")

    lines.append("---")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_full_report(
    ideas: list[IdeeNote],
    vault_path: Path,
    target_tier: int = 0,
) -> str:
    """Build a full markdown report for the given list of ideas.

    Loads linked ChanceNotes and WissenNotes from vault to include their details.
    Ideas should already be sorted by rank (descending score).
    target_tier: if set, ideas below this tier are flagged as candidates without data.
    """
    today = datetime.date.today().isoformat()

    # Load linked note details from vault
    chance_notes_by_id: dict[str, ChanceNote] = {
        vn.model.id: vn.model
        for vn in list_notes(vault_path, ChanceNote).notes
    }
    wissen_notes_by_id: dict[str, WissenNote] = {
        vn.model.id: vn.model
        for vn in list_notes(vault_path, WissenNote).notes
    }

    header_lines = [
        f"# Full Idea Report — {today}",
        "",
        f"**{len(ideas)} ideas** · Generated by `ideapipe full-report`",
        "",
        "*Score scale: 1 = best, 6 = worst (inverted log scale)*",
        "",
        "---",
        "",
    ]

    idea_sections: list[str] = []
    for rank, idea in enumerate(ideas, 1):
        section = _render_idea_section(
            rank=rank,
            idea=idea,
            chance_notes_by_id=chance_notes_by_id,
            wissen_notes_by_id=wissen_notes_by_id,
            target_tier=target_tier,
        )
        idea_sections.append(section)

    return "\n".join(header_lines) + "\n".join(idea_sections)
