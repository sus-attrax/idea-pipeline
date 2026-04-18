"""Vault I/O: read, write, and list Obsidian markdown notes.

Core contract for the entire pipeline:
- read_note()  → validated Pydantic model + body + path
- write_note() → atomic (temp + rename), wikilinks reconstructed
- list_notes() → iterate over notes of a given type
- check_links() → find broken wikilink references

Design decisions:
- VaultNote bundles model + body + path so nothing gets lost in transit
- Write is always atomic — never a half-written file on disk
- Wikilink fields are reconstructed on write ([[id]] format)
- Unknown fields survive read-write cycles (extra="allow" in Pydantic)
- YAML format after write may differ cosmetically from input
  (quote style, key order) but data is identical. If exact format
  preservation becomes important, swap pyyaml for ruamel.yaml.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import frontmatter
from pydantic import ValidationError

from idea_pipeline.schemas import (
    BaseNote,
    ChanceNote,
    IdeeNote,
    WissenNote,
    detect_note_type,
)


# ---------------------------------------------------------------------------
# VaultNote: the object that flows through the pipeline
# ---------------------------------------------------------------------------

@dataclass
class VaultNote:
    """A note read from (or to be written to) the vault.

    Bundles three things that always travel together:
    - model:  validated Pydantic schema (frontmatter data)
    - body:   markdown content below the YAML frontmatter
    - path:   file location in the vault
    """

    model: BaseNote
    body: str
    path: Path


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_note(path: Path) -> VaultNote:
    """Read a single markdown note, validate against the detected schema.

    Raises:
        FileNotFoundError: if the file doesn't exist
        ValueError: if note type can't be detected from `database` field
        pydantic.ValidationError: if frontmatter data fails schema validation
    """
    if not path.exists():
        raise FileNotFoundError(f"Note not found: {path}")

    post = frontmatter.load(path)

    schema_cls = detect_note_type(post.metadata)
    if schema_cls is None:
        raise ValueError(
            f"Cannot detect note type for '{path.name}' "
            f"(no recognized database field in frontmatter)"
        )

    data = dict(post.metadata)
    data["id"] = path.stem  # ID is always the filename without extension

    model = schema_cls.model_validate(data)
    return VaultNote(model=model, body=post.content.strip(), path=path)


# ---------------------------------------------------------------------------
# Write (atomic)
# ---------------------------------------------------------------------------

# YAML keys whose list values must be wrapped in [[]] on write.
# These are the keys AS THEY APPEAR IN YAML (i.e., aliases, not Python names).
_WIKILINK_YAML_KEYS = frozenset({
    "database",
    "chancen",
    "wissen",
    "umprella_problem",  # alias of umbrella_problem
})


def _to_yaml_dict(note: BaseNote) -> dict:
    """Convert a Pydantic model to a YAML-serializable dict.

    Handles:
    - Alias serialization (credebility, umprella_problem → preserved in YAML)
    - Wikilink reconstruction ([[id]] format for link fields)
    - id exclusion (derived from filename, never stored in YAML)
    - None → empty string (avoids 'null' in YAML, cleaner for Obsidian)
    """
    # by_alias=True: credebility, umprella_problem preserved as YAML keys
    # exclude id: derived from filename, not YAML data
    data = note.model_dump(by_alias=True, exclude={"id"})

    # Reconstruct wikilinks: bare IDs → [[id]]
    for key in _WIKILINK_YAML_KEYS:
        if key in data and isinstance(data[key], list):
            data[key] = [f"[[{v}]]" for v in data[key] if v]

    # Pipeline-managed fields: remove from YAML entirely if not yet set.
    # These only appear once the pipeline has actually computed them.
    _PIPELINE_FIELDS = {
        "score", "score_breakdown", "score_version", "scored_at", "research_fidelity",
    }
    for key in _PIPELINE_FIELDS:
        if key in data and (data[key] is None or data[key] == "" or data[key] == {}):
            del data[key]

    # Remaining None values → empty string for cleaner YAML
    # (Obsidian prefers `field: ` over `field: null`)
    for key, value in list(data.items()):
        if value is None:
            data[key] = ""

    return data


def write_note(vault_note: VaultNote) -> None:
    """Write a note atomically: temp file → rename.

    The rename operation is atomic on Linux (POSIX guarantee).
    Either the old file is fully intact, or the new file is fully written.
    Never a half-written state.

    The body (markdown below frontmatter) is preserved as-is.
    """
    yaml_data = _to_yaml_dict(vault_note.model)
    post = frontmatter.Post(vault_note.body, **yaml_data)

    # Serialize to string first (avoids bytes vs str issues with yaml dumper)
    content = frontmatter.dumps(post, default_flow_style=False, allow_unicode=True)

    # Write to temp file in the SAME directory (important: rename is only
    # atomic within the same filesystem/mount point)
    parent = vault_note.path.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path_str = tempfile.mkstemp(
        suffix=".md.tmp",
        dir=str(parent),
    )
    tmp_path = Path(tmp_path_str)

    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        # Atomic rename
        tmp_path.rename(vault_note.path)
    except Exception:
        # Clean up temp file on failure
        tmp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@dataclass
class ListResult:
    """Result of listing notes in a vault directory."""

    notes: list[VaultNote] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (filename, reason)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (filename, error_msg)


def list_notes(
    vault_path: Path,
    note_type: Optional[type[BaseNote]] = None,
) -> ListResult:
    """List all valid notes in a vault directory (flat, non-recursive).

    Args:
        vault_path: path to the vault root
        note_type: optional filter (IdeeNote, ChanceNote, WissenNote).
                   None = return all recognized notes.

    Returns:
        ListResult with notes, skipped files, and error files.
    """
    result = ListResult()

    if not vault_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {vault_path}")

    for md_file in sorted(vault_path.glob("*.md")):
        try:
            vnote = read_note(md_file)
        except ValueError as e:
            # Unrecognized type (no database field, etc.)
            result.skipped.append((md_file.name, str(e)))
            continue
        except ValidationError as e:
            # Schema validation failure
            first_err = e.errors()[0]
            loc = ".".join(str(p) for p in first_err["loc"])
            result.errors.append((md_file.name, f"{loc}: {first_err['msg']}"))
            continue
        except Exception as e:
            result.errors.append((md_file.name, str(e)))
            continue

        # Type filter
        if note_type is not None and not isinstance(vnote.model, note_type):
            continue

        result.notes.append(vnote)

    return result


# ---------------------------------------------------------------------------
# Data quality checks (vault doctor)
# ---------------------------------------------------------------------------

@dataclass
class DoctorFinding:
    """One data quality issue found by the vault doctor."""

    severity: str  # "error" | "warning" | "info"
    file: str  # filename
    message: str


def check_vault_health(vault_path: Path) -> list[DoctorFinding]:
    """Run data quality checks across the entire vault.

    Checks:
    1. Broken links: wikilinks pointing to nonexistent files
    2. Empty descriptions: notes without meaningful descriptions
    3. Unscored notes: all score fields at default (6)
    4. Untyped files: .md files that can't be classified
    """
    findings: list[DoctorFinding] = []

    # First: build a set of all known note IDs (filenames without .md)
    all_ids = {f.stem for f in vault_path.glob("*.md")}

    # Read all notes
    lr = list_notes(vault_path)

    # Report errors from reading
    for filename, error in lr.errors:
        findings.append(DoctorFinding("error", filename, f"Parse error: {error}"))

    # Report untyped files
    for filename, reason in lr.skipped:
        findings.append(DoctorFinding("warning", filename, "Unknown type (no database field)"))

    for vnote in lr.notes:
        model = vnote.model
        fname = vnote.path.name

        # Check 1: Broken links
        _check_broken_links(model, fname, all_ids, findings)

        # Check 2: Empty descriptions
        desc = getattr(model, "description", None)
        if not desc or (isinstance(desc, str) and desc.strip() in ("", "...")):
            findings.append(DoctorFinding("info", fname, "No description"))

        # Check 3: Unscored notes (all scores at default 6)
        _check_unscored(model, fname, findings)

    return findings


def _check_broken_links(
    model: BaseNote, fname: str, all_ids: set[str], findings: list[DoctorFinding]
) -> None:
    """Check wikilink fields for references to nonexistent notes."""
    link_fields: list[tuple[str, list[str]]] = []

    if isinstance(model, IdeeNote):
        link_fields.append(("chancen", model.chancen))
        link_fields.append(("wissen", model.wissen))
    elif isinstance(model, ChanceNote):
        link_fields.append(("umprella_problem", model.umbrella_problem))

    for field_name, linked_ids in link_fields:
        for linked_id in linked_ids:
            if linked_id not in all_ids:
                findings.append(DoctorFinding(
                    "warning",
                    fname,
                    f"Broken link in {field_name}: [[{linked_id}]] "
                    f"(no file '{linked_id}.md' in vault)"
                ))


def _check_unscored(model: BaseNote, fname: str, findings: list[DoctorFinding]) -> None:
    """Check if all score fields are still at default value (6 = worst)."""
    score_fields: list[str] = []

    if isinstance(model, IdeeNote):
        score_fields = [
            "market_size", "market_potential", "impact",
            "difficulty", "time_investment", "innovativeness",
        ]
    elif isinstance(model, ChanceNote):
        score_fields = [
            "granularitaet", "urgency", "prevalence",
            "impact", "personal_experience", "market_awareness",
        ]
    elif isinstance(model, WissenNote):
        score_fields = ["enjoyment", "confidence", "credibility", "contacts"]

    if not score_fields:
        return

    values = [getattr(model, f, 6) for f in score_fields]
    if all(v == 6 for v in values):
        findings.append(DoctorFinding(
            "info", fname, "All scores at default (6) — not yet rated"
        ))
