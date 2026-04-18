# Step 7: Idee↔Wissen Linking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `ideapipe link` command that uses an LLM to suggest which WissenNotes (personal knowledge areas) are relevant to each IdeeNote, and writes those links back into the idea files.

**Architecture:** New `src/idea_pipeline/link.py` module mirrors the structure of `enrich.py`. Single LLM phase: batch 10 ideas per Haiku call, each call receives all WissenNote IDs+descriptions as context and returns JSON mapping idea_id → [wissen_ids]. Idempotent: ideas that already have wissen links are skipped. CLI command added to `cli.py`.

**Tech Stack:** Python 3.11, Typer, anthropic SDK (Haiku), Pydantic v2, existing `vault_io.list_notes` / `write_note` / `VaultNote`.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `config/prompts/v1/link_wissen.txt` | System prompt for wissen-matching LLM call |
| Create | `src/idea_pipeline/link.py` | `run_link()` function + `LinkResult` dataclass |
| Modify | `src/idea_pipeline/cli.py` | Add `link` command (after `enrich` command) |

---

### Task 1: Write the prompt file

**Files:**
- Create: `config/prompts/v1/link_wissen.txt`

- [ ] **Step 1: Create prompt**

```
You match business ideas to relevant personal knowledge areas (Wissen).

Input: JSON object with:
  - "ideas": list of {"id": "...", "description": "..."}
  - "wissen": list of {"id": "...", "description": "..."}

Output: JSON list of {"idea_id": "...", "wissen_ids": ["id1", "id2", ...]}

Rules:
- For each idea, select 1-5 wissen areas that are directly relevant
- Only include wissen where the person's knowledge/contacts genuinely help build or sell this idea
- Prefer quality over quantity — if only 1 wissen area truly fits, return just 1
- Use the exact wissen IDs from the input list
- Omit ideas where no wissen area is relevant (do not emit {"idea_id": "...", "wissen_ids": []})
- Output ONLY valid JSON list — no explanation, no markdown fences
```

- [ ] **Step 2: Verify file exists**

```bash
cat /home/homo/idea-pipeline/config/prompts/v1/link_wissen.txt
```

Expected: prompt text printed without error.

- [ ] **Step 3: Commit**

```bash
cd /home/homo/idea-pipeline
git add config/prompts/v1/link_wissen.txt
git commit -m "step 7: add link_wissen prompt"
```

---

### Task 2: Implement `link.py`

**Files:**
- Create: `src/idea_pipeline/link.py`

- [ ] **Step 1: Write `link.py`**

```python
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
    linked: list[tuple[str, list[str]]] = field(default_factory=list)   # (idea_id, [wissen_ids])
    skipped: list[str] = field(default_factory=list)                     # already had links
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

    # Split into already-linked and needs-linking
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

    # index VaultNotes by id for fast lookup
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
            # Validate: only keep IDs that actually exist
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
```

- [ ] **Step 2: Smoke-test import**

```bash
cd /home/homo/idea-pipeline && source .venv/bin/activate && python3 -c "from idea_pipeline.link import run_link; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/idea_pipeline/link.py
git commit -m "step 7: implement link.py — wissen linking logic"
```

---

### Task 3: Add `link` command to CLI

**Files:**
- Modify: `src/idea_pipeline/cli.py` — add import + `link_cmd` after `enrich_cmd`

- [ ] **Step 1: Add import** (at top of cli.py with other imports)

Add after `from idea_pipeline.enrich import EnrichResult, run_enrich`:
```python
from idea_pipeline.link import LinkResult, run_link
```

- [ ] **Step 2: Add command** (after the closing of `enrich_cmd`)

```python
@app.command("link")
def link_cmd(
    vault: Optional[Path] = _vault_option,
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without writing"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Link ideas to relevant personal knowledge areas (Wissen).

    Idempotent: ideas that already have wissen links are skipped.
    Batches 10 ideas per LLM call (claude-haiku).
    """
    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    if dry_run:
        console.print("[bold yellow]Dry run[/bold yellow] — no files will be written.\n")

    console.print(f"Linking wissen for [cyan]{vault_path}[/cyan] ...\n")

    try:
        result = run_link(vault_path, dry_run=dry_run, verbose=verbose)
    except Exception as e:
        console.print(f"[red]✗ Link failed:[/red] {e}")
        raise typer.Exit(1)

    if result.skipped and verbose:
        console.print(f"[dim]Skipped {len(result.skipped)} (already linked)[/dim]")

    if result.linked:
        label = "would link" if dry_run else "linked"
        console.print(f"[bold]Wissen links[/bold] ({len(result.linked)} ideas {label}):")
        for idea_id, wids in result.linked:
            prefix = "[dim]~[/dim]" if dry_run else "[green]→[/green]"
            wissen_str = ", ".join(wids) if wids else "(to be generated)"
            console.print(f"  {prefix} {idea_id}: {wissen_str}")

    if result.errors:
        console.print(f"[red]Errors ({len(result.errors)}):[/red]")
        for iid, msg in result.errors:
            console.print(f"  [red]✗[/red] {iid}: {msg}")

    total = len(result.linked)
    skipped = len(result.skipped)
    errors = len(result.errors)
    console.print(f"\nDone. {total} linked · {skipped} skipped · {errors} errors")
```

- [ ] **Step 3: Test dry-run**

```bash
cd /home/homo/idea-pipeline && source .venv/bin/activate && ideapipe link --dry-run
```

Expected: output listing ~142 ideas that would be linked, 0 errors.

- [ ] **Step 4: Run for real**

```bash
source .venv/bin/activate && ideapipe link --verbose 2>&1
```

Expected: all ideas get 1-5 wissen links, 0 errors.

- [ ] **Step 5: Verify vault doctor improved**

```bash
source .venv/bin/activate && ideapipe vault doctor 2>&1 | grep -E "^(Errors|Warnings|Info)"
```

- [ ] **Step 6: Commit everything**

```bash
git add src/idea_pipeline/cli.py
git commit -m "step 7: add ideapipe link command"
git push
```
