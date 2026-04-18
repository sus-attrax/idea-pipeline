"""Ingest: create vault notes from simple name:description pairs.

This is the "front door" for getting ideas into the vault quickly.
You provide a list of ideas (name + description), and the pipeline
creates properly structured notes from them.

Supports three note types:
- idee (default): creates IdeeNote with all score defaults
- chance: creates ChanceNote
- wissen: creates WissenNote (scores must be filled manually later)

Input format (one per line):
  name: description text here
  another_name: another description

Names are sanitized into valid filenames (lowercase, underscores,
no special characters). If a file already exists, it is skipped
(idempotent — safe to run multiple times).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from idea_pipeline.schemas import BaseNote, ChanceNote, IdeeNote, WissenNote
from idea_pipeline.vault_io import VaultNote, write_note


@dataclass
class IngestItem:
    """A single parsed name:description pair."""

    name: str  # raw name from input
    filename: str  # sanitized filename (without .md)
    description: str


@dataclass
class IngestResult:
    """Summary of an ingest operation."""

    created: list[str]  # filenames of newly created notes
    skipped: list[str]  # filenames that already existed
    errors: list[tuple[str, str]]  # (filename, error message)


def sanitize_filename(name: str) -> str:
    """Convert a human-readable name into a valid vault filename.

    Rules:
    - Lowercase
    - Spaces and hyphens → underscores
    - Remove everything that isn't alphanumeric, underscore, or period
    - Collapse multiple underscores
    - Strip leading/trailing underscores
    - Transliterate unicode (ä→ae, ü→ue, etc.) via NFKD decomposition

    Examples:
        "Urban Mushroom Farm" → "urban_mushroom_farm"
        "Balkongärten (v2)"  → "balkongarten_v2"
        "my--weird   name!"  → "my_weird_name"
    """
    # Normalize unicode: decompose, strip combining marks
    normalized = unicodedata.normalize("NFKD", name)
    ascii_ish = normalized.encode("ascii", errors="ignore").decode("ascii")

    # Lowercase, replace spaces/hyphens with underscores
    result = ascii_ish.lower()
    result = re.sub(r"[\s\-]+", "_", result)

    # Remove anything that's not alphanumeric or underscore
    result = re.sub(r"[^a-z0-9_]", "", result)

    # Collapse multiple underscores, strip edges
    result = re.sub(r"_+", "_", result)
    result = result.strip("_")

    return result or "unnamed"


def parse_ingest_input(text: str) -> list[IngestItem]:
    """Parse multiline text into IngestItem list.

    Expected format (one per line):
        name: description text here
        another name: another description

    Lines starting with # are comments. Empty lines are skipped.
    If a line has no colon, the entire line is treated as the name
    with an empty description.
    """
    items: list[IngestItem] = []

    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line:
            name_part, desc_part = line.split(":", 1)
            name_part = name_part.strip()
            desc_part = desc_part.strip()
        else:
            name_part = line
            desc_part = ""

        if not name_part:
            continue

        filename = sanitize_filename(name_part)
        items.append(IngestItem(
            name=name_part,
            filename=filename,
            description=desc_part,
        ))

    return items


def _make_model(
    note_type: str,
    item: IngestItem,
) -> BaseNote:
    """Create a Pydantic model from an IngestItem.

    Returns a model with all defaults set and description filled in.
    """
    common = {
        "id": item.filename,
        "status": "roh",
        "description": item.description or None,
    }

    if note_type == "idee":
        return IdeeNote(
            database=["geschaeftsideen🎲"],
            **common,
        )
    elif note_type == "chance":
        return ChanceNote(
            database=["chancen🌋"],
            status="neu",
            **{k: v for k, v in common.items() if k != "status"},
        )
    elif note_type == "wissen":
        return WissenNote(
            database=["wissen🤯"],
            status="neu",
            **{k: v for k, v in common.items() if k != "status"},
        )
    else:
        raise ValueError(f"Unknown note type: {note_type}. Use: idee, chance, wissen")


def ingest(
    text: str,
    vault_path: Path,
    note_type: str = "idee",
    dry_run: bool = False,
) -> IngestResult:
    """Create vault notes from a multiline name:description input.

    Args:
        text: multiline input, one "name: description" per line
        vault_path: path to the vault directory
        note_type: "idee", "chance", or "wissen"
        dry_run: if True, don't write files, just report what would happen

    Returns:
        IngestResult with created/skipped/error counts
    """
    items = parse_ingest_input(text)
    result = IngestResult(created=[], skipped=[], errors=[])

    for item in items:
        target = vault_path / f"{item.filename}.md"

        # Idempotent: skip if file exists
        if target.exists():
            result.skipped.append(item.filename)
            continue

        try:
            model = _make_model(note_type, item)

            if not dry_run:
                vnote = VaultNote(model=model, body="", path=target)
                write_note(vnote)

            result.created.append(item.filename)
        except Exception as e:
            result.errors.append((item.filename, str(e)))

    return result
