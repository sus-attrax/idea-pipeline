"""Step 6: LLM-Enrichment — chance generation and descriptions.

Four phases, all idempotent:
  1. Stubs       — create minimal ChanceNote for every broken chance link
  2. Generation  — for ideas with zero chance links, LLM suggests 3-6 chances
  3. Descriptions — batch-generate 1-2 sentence descriptions for undescribed chances
  4. Umbrella    — LLM suggests umbrella_problem hierarchy links

All LLM calls: batched JSON output, model=haiku (cheap classification tasks).
Results written back to vault with research_fidelity=tier0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from idea_pipeline.schemas import ChanceNote, IdeeNote
from idea_pipeline.vault_io import VaultNote, list_notes, write_note

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PROMPTS_DIR = _PROJECT_ROOT / "config" / "prompts" / "v1"

_HAIKU = "claude-haiku-4-5-20251001"
_BATCH_SIZE = 10


@dataclass
class EnrichResult:
    stubs_created: list[str] = field(default_factory=list)
    chances_linked: list[tuple[str, list[str]]] = field(default_factory=list)
    descriptions_written: list[str] = field(default_factory=list)
    umbrellas_written: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_client():
    from anthropic import Anthropic
    return Anthropic()


def _read_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _batched(items: list, size: int = _BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _parse_json_response(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        # strip markdown code fence
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def _llm_call(client, system: str, user: Any) -> str:
    resp = client.messages.create(
        model=_HAIKU,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
    )
    return resp.content[0].text


def _make_chance_stub(cid: str, vault_path: Path, description: str | None = None) -> VaultNote:
    model = ChanceNote(
        id=cid,
        database=["chancen🌋"],
        status="neu",
        description=description,
    )
    return VaultNote(model=model, body="", path=vault_path / f"{cid}.md")


def _get_linked_idea_ids(chance_id: str, all_ideen: list[VaultNote]) -> list[str]:
    return [v.model.id for v in all_ideen if chance_id in v.model.chancen]


# ---------------------------------------------------------------------------
# Phase 3: description generation
# ---------------------------------------------------------------------------

def _generate_descriptions(
    items: list[dict], client
) -> dict[str, str]:
    """Batch-generate descriptions. Returns {chance_id: description}."""
    system = _read_prompt("enrich_chances.txt")
    results: dict[str, str] = {}
    for batch in _batched(items):
        try:
            raw = _llm_call(client, system, batch)
            data = _parse_json_response(raw)
            for entry in data:
                results[entry["id"]] = entry["description"]
        except Exception as e:
            for entry in batch:
                results[f"__error__{entry['id']}"] = str(e)
    return results


# ---------------------------------------------------------------------------
# Phase 2: chance generation for ideas without links
# ---------------------------------------------------------------------------

def _generate_chances_for_idea(
    idea_id: str, idea_desc: str, existing: list[dict], client
) -> dict:
    """Returns {"linked_existing": [...], "new_chances": [{id, description}]}."""
    system = _read_prompt("generate_chances.txt")
    payload = {
        "idea_id": idea_id,
        "idea_description": idea_desc,
        "existing_chances": existing,
    }
    raw = _llm_call(client, system, payload)
    return _parse_json_response(raw)


# ---------------------------------------------------------------------------
# Phase 4: umbrella links
# ---------------------------------------------------------------------------

def _suggest_umbrella_links(
    all_chances: list[dict], client
) -> dict[str, list[str]]:
    """Returns {chance_id: [umbrella_id, ...]}."""
    if not all_chances:
        return {}
    system = _read_prompt("umbrella_links.txt")
    try:
        raw = _llm_call(client, system, all_chances)
        data = _parse_json_response(raw)
        return {
            item["id"]: item["umbrella_ids"]
            for item in data
            if item.get("umbrella_ids")
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_enrich(
    vault_path: Path,
    dry_run: bool = False,
    verbose: bool = False,
    skip_umbrella: bool = False,
) -> EnrichResult:
    result = EnrichResult()

    # Load vault (no API client needed until Phase 2+)
    ideen = list_notes(vault_path, IdeeNote).notes
    chancen_by_id: dict[str, VaultNote] = {
        n.model.id: n for n in list_notes(vault_path, ChanceNote).notes
    }

    # ------------------------------------------------------------------
    # Phase 1: create stubs for broken chance links (no LLM needed)
    # ------------------------------------------------------------------
    missing: set[str] = set()
    for vnote in ideen:
        for cid in vnote.model.chancen:
            if cid not in chancen_by_id:
                missing.add(cid)

    for cid in sorted(missing):
        stub = _make_chance_stub(cid, vault_path)
        if not dry_run:
            write_note(stub)
            chancen_by_id[cid] = stub
        result.stubs_created.append(cid)
        if verbose:
            print(f"  stub: {cid}")

    # In dry-run mode: skip all LLM phases, just report what would be touched.
    if dry_run:
        ideas_without_chances = [v for v in ideen if not v.model.chancen]
        result.chances_linked = [(v.model.id, []) for v in ideas_without_chances]

        needs_desc_count = sum(
            1 for n in chancen_by_id.values() if not n.model.description
        )
        # Represent as placeholder list for the summary
        result.descriptions_written = [f"(~{needs_desc_count} chances)"]

        needs_umbrella_count = sum(
            1 for n in chancen_by_id.values() if not n.model.umbrella_problem
        ) if not skip_umbrella else 0
        result.umbrellas_written = [f"(~{needs_umbrella_count} chances)"] if needs_umbrella_count else []

        return result

    # Real run — need the API client from here on
    client = _get_client()

    # ------------------------------------------------------------------
    # Phase 2: generate chances for ideas with no links at all
    # ------------------------------------------------------------------
    ideas_without_chances = [v for v in ideen if not v.model.chancen]

    if ideas_without_chances:
        existing_list = [
            {
                "id": cid,
                "description": n.model.description or cid.replace("_", " "),
            }
            for cid, n in chancen_by_id.items()
        ]
        for vnote in ideas_without_chances:
            idea = vnote.model
            try:
                suggestion = _generate_chances_for_idea(
                    idea.id, idea.description or "", existing_list, client
                )
            except Exception as e:
                result.errors.append((idea.id, f"chance generation failed: {e}"))
                continue

            new_links: list[str] = list(suggestion.get("linked_existing", []))

            for new_c in suggestion.get("new_chances", []):
                cid = new_c["id"]
                if cid in chancen_by_id:
                    new_links.append(cid)
                    continue
                stub = _make_chance_stub(cid, vault_path, new_c.get("description"))
                write_note(stub)
                chancen_by_id[cid] = stub
                result.stubs_created.append(cid)
                new_links.append(cid)
                existing_list.append({"id": cid, "description": new_c.get("description", "")})

            idea.chancen = new_links
            write_note(VaultNote(model=idea, body=vnote.body, path=vnote.path))
            result.chances_linked.append((idea.id, new_links))

    # ------------------------------------------------------------------
    # Phase 3: descriptions for undescribed chances
    # ------------------------------------------------------------------
    needs_desc = [
        {
            "id": cid,
            "name": cid.replace("_", " "),
            "linked_ideas": _get_linked_idea_ids(cid, ideen),
        }
        for cid, n in chancen_by_id.items()
        if not n.model.description
    ]

    if needs_desc:
        desc_map = _generate_descriptions(needs_desc, client)
        for cid, desc in desc_map.items():
            if cid.startswith("__error__"):
                real_id = cid[len("__error__"):]
                result.errors.append((real_id, f"description generation failed: {desc}"))
                continue
            if cid not in chancen_by_id:
                continue
            vnote = chancen_by_id[cid]
            vnote.model.description = desc
            vnote.model.research_fidelity = "tier0"
            write_note(vnote)
            result.descriptions_written.append(cid)

    # ------------------------------------------------------------------
    # Phase 4: umbrella problem links
    # ------------------------------------------------------------------
    if not skip_umbrella:
        all_chance_list = [
            {
                "id": cid,
                "description": n.model.description or cid.replace("_", " "),
            }
            for cid, n in chancen_by_id.items()
        ]
        try:
            umbrella_map = _suggest_umbrella_links(all_chance_list, client)
        except Exception as e:
            result.errors.append(("umbrella_phase", str(e)))
            umbrella_map = {}

        for cid, umbrella_ids in umbrella_map.items():
            if cid not in chancen_by_id:
                continue
            vnote = chancen_by_id[cid]
            if vnote.model.umbrella_problem:
                continue  # already set — idempotent
            valid = [uid for uid in umbrella_ids if uid in chancen_by_id]
            if valid:
                vnote.model.umbrella_problem = valid
                write_note(vnote)
                result.umbrellas_written.append(cid)

    return result
