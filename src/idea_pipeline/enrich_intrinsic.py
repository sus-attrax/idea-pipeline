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
from idea_pipeline.vault_io import list_notes, write_note

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
    if text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
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
