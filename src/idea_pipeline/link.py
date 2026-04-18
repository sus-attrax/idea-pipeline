"""Step 7: LLM linking — match ideas to personal knowledge areas (Wissen).

Single phase, idempotent:
  - Ideas that already have wissen links are skipped.
  - Remaining ideas are batched (10/call) and sent to Haiku with the full
    list of WissenNotes as context.
  - Suggested wissen IDs are validated against known wissen before writing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from idea_pipeline.schemas import IdeeNote, WissenNote
from idea_pipeline.vault_io import VaultNote, list_notes, write_note

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PROMPTS_DIR = _PROJECT_ROOT / "config" / "prompts" / "v1"

_HAIKU = "claude-haiku-4-5-20251001"
_BATCH_SIZE = 10


@dataclass
class LinkResult:
    linked: list[tuple[str, list[str]]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def _get_client():
    from anthropic import Anthropic
    return Anthropic()


def _read_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _batched(items: list, size: int = _BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _parse_json_response(text: str) -> list:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def run_link(
    vault_path: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> LinkResult:
    result = LinkResult()

    ideen = list_notes(vault_path, IdeeNote).notes
    wissen_notes = list_notes(vault_path, WissenNote).notes
    known_wissen_ids = {n.model.id for n in wissen_notes}

    wissen_list = [
        {"id": n.model.id, "description": n.model.description or n.model.id.replace("_", " ")}
        for n in wissen_notes
    ]

    to_link: list[VaultNote] = []
    for vnote in ideen:
        if vnote.model.wissen:
            result.skipped.append(vnote.model.id)
        else:
            to_link.append(vnote)

    if verbose:
        print(f"  {len(result.skipped)} ideas already linked, {len(to_link)} to process")

    if dry_run or not to_link:
        result.linked = [(v.model.id, []) for v in to_link]
        return result

    client = _get_client()
    system = _read_prompt("link_wissen.txt")
    vnote_by_id = {v.model.id: v for v in to_link}

    for batch in _batched(to_link):
        ideas_payload = [
            {"id": v.model.id, "description": v.model.description or ""}
            for v in batch
        ]
        payload = {"ideas": ideas_payload, "wissen": wissen_list}

        try:
            resp = client.messages.create(
                model=_HAIKU,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
            data = _parse_json_response(resp.content[0].text)
        except Exception as e:
            for v in batch:
                result.errors.append((v.model.id, str(e)))
            continue

        for entry in data:
            idea_id = entry.get("idea_id", "")
            raw_ids = entry.get("wissen_ids", [])
            valid_ids = [wid for wid in raw_ids if wid in known_wissen_ids]
            if not valid_ids or idea_id not in vnote_by_id:
                continue
            vnote = vnote_by_id[idea_id]
            vnote.model.wissen = valid_ids
            write_note(vnote)
            result.linked.append((idea_id, valid_ids))
            if verbose:
                print(f"  → {idea_id}: {', '.join(valid_ids)}")

    return result
