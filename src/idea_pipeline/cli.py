"""CLI entry point for the idea pipeline.

Commands grow with each pipeline step:
  Step 1: hello, info
  Step 2: schema check, schema check-dir
  Step 3: vault read, vault list, vault doctor
  Step 5: ingest (create notes from name:description pairs)

Each command is an isolated, idempotent step — the contract between
you (and later, Claude Code) and the system.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import frontmatter
import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from idea_pipeline.enrich import EnrichResult, run_enrich
from idea_pipeline.enrich_intrinsic import EnrichIntrinsicResult, run_intrinsic_enrich
from idea_pipeline.generator import GenerateResult, _select_path_b_candidates, run_generate_domain
from idea_pipeline.link import LinkResult, run_link
from idea_pipeline.scoring import ScoreResult, score_vault
from idea_pipeline.research.cache import cache_stats
from idea_pipeline.ingest import IngestResult, ingest, parse_ingest_input
from idea_pipeline.schemas import (
    BaseNote,
    ChanceNote,
    IdeeNote,
    WissenNote,
    detect_note_type,
)
from idea_pipeline.settings import get_vault_path
from idea_pipeline.vault_io import (
    DoctorFinding,
    ListResult,
    VaultNote,
    check_vault_health,
    list_notes,
    read_note,
    write_note,
)

app = typer.Typer(
    name="ideapipe",
    help="Business idea validation & generation pipeline.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

# --- Top-level commands ------------------------------------------------------

@app.command()
def hello(name: str = "Meister") -> None:
    """Smoke test."""
    console.print(f"[bold green]✓[/bold green] Pipeline lebt. Hallo, {name}.")


@app.command()
def info() -> None:
    """Show pipeline status and configured paths."""
    from idea_pipeline import __version__

    vault = get_vault_path()
    console.print(f"[bold]idea-pipeline[/bold] v{__version__}")
    console.print(f"Vault path: [cyan]{vault}[/cyan]")
    if vault.is_dir():
        md_count = len(list(vault.glob("*.md")))
        console.print(f"Notes found: {md_count}")
    else:
        console.print("[yellow]Vault directory does not exist yet.[/yellow]")


# --- Schema commands ---------------------------------------------------------

schema_app = typer.Typer(help="Schema validation utilities.", no_args_is_help=True)
app.add_typer(schema_app, name="schema")


def _validate_one(file: Path) -> tuple[str, object | None, str | None]:
    """Validate one note. Returns (status, model_or_None, error_msg_or_None)."""
    try:
        post = frontmatter.load(file)
    except Exception as e:
        return ("invalid", None, f"frontmatter parse failed: {e}")
    schema_cls = detect_note_type(post.metadata)
    if schema_cls is None:
        return ("unknown_type", None, None)
    try:
        data = dict(post.metadata)
        data["id"] = file.stem
        note = schema_cls.model_validate(data)
        return ("valid", note, None)
    except ValidationError as e:
        first_err = e.errors()[0]
        loc = ".".join(str(p) for p in first_err["loc"])
        return ("invalid", None, f"{loc}: {first_err['msg']}")


@schema_app.command("check")
def schema_check(
    file: Path = typer.Argument(..., help="Path to a markdown note file"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Validate a single note against its detected schema."""
    if not file.exists():
        console.print(f"[red]✗ File not found:[/red] {file}")
        raise typer.Exit(1)

    status, note, err = _validate_one(file)
    if status == "unknown_type":
        console.print(f"[yellow]?[/yellow] {file.name}: no recognized database field")
        raise typer.Exit(2)
    if status == "invalid":
        console.print(f"[red]✗[/red] {file.name}: {err}")
        raise typer.Exit(1)

    cls_name = type(note).__name__
    console.print(f"[bold green]✓[/bold green] {file.name} → [bold]{cls_name}[/bold]")

    if verbose:
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Field", style="dim")
        table.add_column("Value")
        for fn, fv in note.model_dump().items():
            if fv is None or fv == [] or fv == "":
                continue
            val_str = str(fv)
            if len(val_str) > 100:
                val_str = val_str[:97] + "..."
            table.add_row(fn, val_str)
        console.print(table)


@schema_app.command("check-dir")
def schema_check_dir(
    directory: Path = typer.Argument(..., help="Directory of markdown notes"),
    show_unknown: bool = typer.Option(False, "--show-unknown"),
) -> None:
    """Batch-validate all .md notes in a directory."""
    if not directory.is_dir():
        console.print(f"[red]✗ Not a directory:[/red] {directory}")
        raise typer.Exit(1)

    files = sorted(directory.glob("*.md"))
    if not files:
        console.print(f"[yellow]No .md files found in {directory}[/yellow]")
        raise typer.Exit(0)

    valid, invalid_list, unknown_list = 0, [], []
    counts: dict[str, int] = {}

    for f in files:
        status, note, err = _validate_one(f)
        if status == "valid":
            valid += 1
            cn = type(note).__name__
            counts[cn] = counts.get(cn, 0) + 1
        elif status == "invalid":
            invalid_list.append((f.name, err or "unknown"))
        else:
            unknown_list.append(f.name)

    console.print(
        f"\n[bold]Summary[/bold] ({len(files)} files): "
        f"[green]{valid} valid[/green] · "
        f"[red]{len(invalid_list)} invalid[/red] · "
        f"[dim]{len(unknown_list)} unknown[/dim]"
    )
    if counts:
        console.print(f"[dim]{' · '.join(f'{k}: {v}' for k, v in sorted(counts.items()))}[/dim]")
    if invalid_list:
        console.print("\n[bold red]Invalid:[/bold red]")
        for fn, msg in invalid_list:
            console.print(f"  [red]✗[/red] {fn}: {msg}")
    if show_unknown and unknown_list:
        console.print("\n[bold yellow]Unknown type:[/bold yellow]")
        for fn in unknown_list:
            console.print(f"  [yellow]?[/yellow] {fn}")
    if invalid_list:
        raise typer.Exit(1)


# --- Vault commands ----------------------------------------------------------

vault_app = typer.Typer(help="Read, list, and inspect vault notes.", no_args_is_help=True)
app.add_typer(vault_app, name="vault")

# Reusable vault path option
_vault_option = typer.Option(
    None, "--vault", "-V",
    help="Vault directory (default: $IDEAPIPE_VAULT or ~/vaults/idea-validation)",
)

_TYPE_MAP = {"idee": IdeeNote, "chance": ChanceNote, "wissen": WissenNote}


@vault_app.command("read")
def vault_read(
    file: Path = typer.Argument(..., help="Path to a note file"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Read a single note and display its parsed data."""
    try:
        vnote = read_note(file)
    except (FileNotFoundError, ValueError, ValidationError) as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)

    model = vnote.model
    cls_name = type(model).__name__
    console.print(f"[bold green]✓[/bold green] {file.name} → [bold]{cls_name}[/bold] (id: {model.id})")

    if vnote.body:
        body_preview = vnote.body[:80].replace("\n", " ")
        if len(vnote.body) > 80:
            body_preview += "..."
        console.print(f"[dim]Body: {body_preview}[/dim]")

    if verbose:
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Field", style="dim")
        table.add_column("Value")
        for fn, fv in model.model_dump().items():
            if fv is None or fv == [] or fv == "":
                continue
            val_str = str(fv)
            if len(val_str) > 120:
                val_str = val_str[:117] + "..."
            table.add_row(fn, val_str)
        console.print(table)


@vault_app.command("list")
def vault_list(
    vault: Optional[Path] = _vault_option,
    note_type: Optional[str] = typer.Option(
        None, "--type", "-t",
        help="Filter by type: idee, chance, wissen",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List all notes in the vault, optionally filtered by type."""
    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    type_cls = None
    if note_type:
        type_cls = _TYPE_MAP.get(note_type.lower())
        if type_cls is None:
            console.print(f"[red]✗ Unknown type:[/red] {note_type}. Use: idee, chance, wissen")
            raise typer.Exit(1)

    lr = list_notes(vault_path, note_type=type_cls)

    # Print notes
    if not lr.notes:
        console.print("[yellow]No matching notes found.[/yellow]")
    else:
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("ID", style="bold")
        table.add_column("Type", style="dim")
        table.add_column("Status")
        if verbose:
            table.add_column("Description")

        for vnote in lr.notes:
            m = vnote.model
            type_label = type(m).__name__.replace("Note", "")
            desc = ""
            if verbose:
                raw_desc = getattr(m, "description", "") or ""
                desc = raw_desc[:60] + ("..." if len(raw_desc) > 60 else "")
            row = [m.id, type_label, m.status or "-"]
            if verbose:
                row.append(desc)
            table.add_row(*row)

        console.print(table)

    # Summary
    console.print(
        f"\n[bold]{len(lr.notes)}[/bold] notes"
        f" · [dim]{len(lr.skipped)} skipped · {len(lr.errors)} errors[/dim]"
    )


@vault_app.command("doctor")
def vault_doctor(
    vault: Optional[Path] = _vault_option,
) -> None:
    """Run data quality checks on the vault.

    Checks for: broken links, empty descriptions, unscored notes,
    untyped files, and parse errors.
    """
    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    console.print(f"Checking [cyan]{vault_path}[/cyan] ...\n")
    findings = check_vault_health(vault_path)

    if not findings:
        console.print("[bold green]✓ No issues found. Vault is clean.[/bold green]")
        return

    # Group by severity
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    infos = [f for f in findings if f.severity == "info"]

    _ICONS = {"error": "[red]✗[/red]", "warning": "[yellow]![/yellow]", "info": "[dim]·[/dim]"}

    for group, label in [(errors, "Errors"), (warnings, "Warnings"), (infos, "Info")]:
        if not group:
            continue
        console.print(f"[bold]{label}[/bold] ({len(group)}):")
        for f in group:
            console.print(f"  {_ICONS[f.severity]} {f.file}: {f.message}")
        console.print()

    console.print(
        f"[bold]Total:[/bold] "
        f"[red]{len(errors)} errors[/red] · "
        f"[yellow]{len(warnings)} warnings[/yellow] · "
        f"[dim]{len(infos)} info[/dim]"
    )


@vault_app.command("write-test")
def vault_write_test(
    file: Path = typer.Argument(..., help="Note to read, write back, and verify"),
) -> None:
    """Round-trip test: read a note → write it back → read again → compare.

    This proves that the read-write cycle doesn't lose or corrupt data.
    The original file is backed up to .md.bak first.
    """
    import shutil

    backup = file.with_suffix(".md.bak")

    try:
        # Step 1: read original
        vnote1 = read_note(file)
        console.print(f"[dim]Read:[/dim] {file.name} → {type(vnote1.model).__name__}")

        # Step 2: backup original
        shutil.copy2(file, backup)

        # Step 3: write back (atomic)
        write_note(vnote1)
        console.print(f"[dim]Wrote:[/dim] {file.name} (atomic)")

        # Step 4: read again
        vnote2 = read_note(file)
        console.print(f"[dim]Re-read:[/dim] {file.name} → {type(vnote2.model).__name__}")

        # Step 5: compare models
        d1 = vnote1.model.model_dump()
        d2 = vnote2.model.model_dump()

        diffs = []
        all_keys = set(d1.keys()) | set(d2.keys())
        for k in sorted(all_keys):
            v1, v2 = d1.get(k), d2.get(k)
            if v1 != v2:
                diffs.append((k, v1, v2))

        if not diffs:
            console.print("[bold green]✓ Round-trip clean — no data loss.[/bold green]")
        else:
            console.print(f"[yellow]⚠ {len(diffs)} field(s) differ after round-trip:[/yellow]")
            for k, v1, v2 in diffs:
                console.print(f"  {k}: {v1!r} → {v2!r}")

        # Restore backup
        shutil.move(str(backup), str(file))
        console.print(f"[dim]Original restored from backup.[/dim]")

    except Exception as e:
        # Restore on failure
        if backup.exists():
            shutil.move(str(backup), str(file))
            console.print(f"[dim]Original restored from backup.[/dim]")
        console.print(f"[red]✗ Error:[/red] {e}")
        raise typer.Exit(1)


# --- Ingest commands ---------------------------------------------------------

@app.command("ingest")
def ingest_cmd(
    text: Optional[str] = typer.Argument(
        None,
        help='Inline "name: description" (one idea). For multiple, use --file or --stdin.',
    ),
    file: Optional[Path] = typer.Option(
        None, "--file", "-f",
        help="File with one 'name: description' per line",
    ),
    stdin: bool = typer.Option(
        False, "--stdin",
        help="Read from stdin (for piping)",
    ),
    note_type: str = typer.Option(
        "idee", "--type", "-t",
        help="Note type to create: idee, chance, wissen",
    ),
    vault: Optional[Path] = _vault_option,
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n",
        help="Preview what would be created without writing files",
    ),
    cascade: bool = typer.Option(
        False, "--cascade/--no-cascade",
        help="Auto-advance ingested ideas through T1→T4 research tiers (default: off)",
    ),
) -> None:
    """Create vault notes from name:description pairs.

    Three ways to provide input:

      # Single idea inline
      ideapipe ingest "urban_mushroom_farm: growing gourmet mushrooms in urban basements"

      # Multiple from a file (one per line)
      ideapipe ingest --file ideas.txt

      # Piped from another command or Claude Code
      echo "idea1: desc1" | ideapipe ingest --stdin

    File format (one per line):
      name: description text here
      another_name: another description
      # lines starting with # are comments

    Idempotent: existing files are skipped, never overwritten.
    """
    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    # Resolve input source
    if file:
        if not file.exists():
            console.print(f"[red]✗ File not found:[/red] {file}")
            raise typer.Exit(1)
        input_text = file.read_text(encoding="utf-8")
    elif stdin:
        input_text = sys.stdin.read()
    elif text:
        input_text = text
    else:
        console.print(
            "[red]✗ No input.[/red] Provide inline text, --file, or --stdin.\n"
            "[dim]Example: ideapipe ingest \"my_idea: a great business idea\"[/dim]"
        )
        raise typer.Exit(1)

    # Preview
    items = parse_ingest_input(input_text)
    if not items:
        console.print("[yellow]No items parsed from input.[/yellow]")
        raise typer.Exit(0)

    if dry_run:
        console.print(f"[bold]Dry run[/bold] — would create {len(items)} {note_type} note(s):\n")
        for item in items:
            target = vault_path / f"{item.filename}.md"
            exists = target.exists()
            status = "[dim]SKIP (exists)[/dim]" if exists else "[green]CREATE[/green]"
            desc_preview = (item.description[:50] + "...") if len(item.description) > 50 else item.description
            console.print(f"  {status} {item.filename}.md — {desc_preview or '[no description]'}")
        return

    # Execute
    result = ingest(input_text, vault_path, note_type=note_type)

    # Report
    if result.created:
        console.print(f"[bold green]✓ Created {len(result.created)} note(s):[/bold green]")
        for fn in result.created:
            console.print(f"  [green]+[/green] {fn}.md")

    if result.skipped:
        console.print(f"[dim]Skipped {len(result.skipped)} (already exist):[/dim]")
        for fn in result.skipped:
            console.print(f"  [dim]–[/dim] {fn}.md")

    if result.errors:
        console.print(f"[red]Errors ({len(result.errors)}):[/red]")
        for fn, msg in result.errors:
            console.print(f"  [red]✗[/red] {fn}: {msg}")
        raise typer.Exit(1)

    if cascade and result.created:
        _run_cascade(result.created, vault_path)


# --- Enrich command ----------------------------------------------------------

@app.command("enrich")
def enrich_cmd(
    vault: Optional[Path] = _vault_option,
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without writing"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    skip_umbrella: bool = typer.Option(False, "--skip-umbrella", help="Skip umbrella_problem phase"),
) -> None:
    """LLM-enrich the vault: generate chance descriptions, fill broken links, set hierarchy.

    Four phases (all idempotent):

    \b
    1. Stubs       — create ChanceNote for every broken chance link in ideas
    2. Generation  — for ideas with no chance links: LLM suggests 3-6 chances
    3. Descriptions — batch-write 1-2 sentence descriptions for undescribed chances
    4. Umbrella    — LLM suggests umbrella_problem hierarchy links

    Uses claude-haiku for all calls. Sets research_fidelity=tier0 on written notes.
    """
    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    if dry_run:
        console.print("[bold yellow]Dry run[/bold yellow] — no files will be written.\n")

    console.print(f"Enriching [cyan]{vault_path}[/cyan] ...\n")

    try:
        result = run_enrich(vault_path, dry_run=dry_run, verbose=verbose, skip_umbrella=skip_umbrella)
    except Exception as e:
        console.print(f"[red]✗ Enrich failed:[/red] {e}")
        raise typer.Exit(1)

    # Phase 1: stubs
    if result.stubs_created:
        console.print(f"[bold]Phase 1 — Stubs[/bold] ({len(result.stubs_created)} created):")
        for cid in result.stubs_created:
            prefix = "[dim]~[/dim]" if dry_run else "[green]+[/green]"
            console.print(f"  {prefix} {cid}.md")

    # Phase 2: new chance links
    if result.chances_linked:
        if dry_run:
            console.print(f"\n[bold]Phase 2 — Links[/bold] ({len(result.chances_linked)} idea(s) without chances — would generate)")
        else:
            console.print(f"\n[bold]Phase 2 — Links[/bold] ({len(result.chances_linked)} ideas linked):")
            for idea_id, links in result.chances_linked:
                console.print(f"  [green]→[/green] {idea_id}: {', '.join(links)}")

    # Phase 3: descriptions
    if result.descriptions_written:
        console.print(f"\n[bold]Phase 3 — Descriptions[/bold] ({len(result.descriptions_written)} written):")
        for cid in result.descriptions_written:
            prefix = "[dim]~[/dim]" if dry_run else "[green]✓[/green]"
            console.print(f"  {prefix} {cid}")

    # Phase 4: umbrella links
    if result.umbrellas_written:
        console.print(f"\n[bold]Phase 4 — Umbrella links[/bold] ({len(result.umbrellas_written)} written):")
        for cid in result.umbrellas_written:
            prefix = "[dim]~[/dim]" if dry_run else "[green]✓[/green]"
            console.print(f"  {prefix} {cid}")

    # Errors
    if result.errors:
        console.print(f"\n[red]Errors ({len(result.errors)}):[/red]")
        for name, msg in result.errors:
            console.print(f"  [red]✗[/red] {name}: {msg}")

    # Summary
    total = (
        len(result.stubs_created)
        + len(result.chances_linked)
        + len(result.descriptions_written)
        + len(result.umbrellas_written)
    )
    action = "Would affect" if dry_run else "Done."
    console.print(
        f"\n[bold]{action}[/bold] "
        f"{len(result.stubs_created)} stubs · "
        f"{len(result.descriptions_written)} descriptions · "
        f"{len(result.umbrellas_written)} umbrella links · "
        f"{len(result.errors)} errors"
    )

    if result.errors:
        raise typer.Exit(1)


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

    console.print(
        f"\nDone. {len(result.linked)} linked · "
        f"{len(result.skipped)} skipped · "
        f"{len(result.errors)} errors"
    )


@app.command("score")
def score_cmd(
    vault: Optional[Path] = _vault_option,
    version: str = typer.Option("v2.1", "--version", help="Scoring version: v1 or v2.1"),
    top_n: Optional[int] = typer.Option(None, "--top", "-n", help="Show only top N ideas"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Compute scores without writing to vault"),
    trigger: str = typer.Option("manual", "--trigger", help="Label for score_history entry"),
    save_as_v1: bool = typer.Option(False, "--save-as-score-v1", help="Also freeze score into score_v1 field"),
) -> None:
    """Score all ideas. Default version: v2.1. Use --version v1 for legacy scoring."""
    import datetime

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    if version == "v1":
        from idea_pipeline.scoring_v1 import score_vault as score_vault_v1
        console.print("[dim]Running v1 scoring...[/dim]")
        result = score_vault_v1(vault_path, dry_run=dry_run, top_n=top_n)

        if save_as_v1 and not dry_run:
            from idea_pipeline.schemas import IdeeNote, ScoreHistoryEntry
            from idea_pipeline.vault_io import list_notes, write_note as _write_note
            rank_map = {iid: i + 1 for i, (iid, _) in enumerate(result.scored)}
            for vnote in list_notes(vault_path, IdeeNote).notes:
                idea = vnote.model
                if idea.score is None:
                    continue
                idea.score_v1 = idea.score
                existing_v1 = [e for e in idea.score_history if e.version == "v1"]
                if not existing_v1:
                    entry = ScoreHistoryEntry(
                        date=datetime.date.today().isoformat(),
                        version="v1",
                        score=idea.score,
                        rank=rank_map.get(idea.id),
                        trigger=trigger,
                    )
                    idea.score_history.append(entry)
                    _write_note(vnote)
            console.print(f"[green]✓[/green] score_v1 frozen for {len(result.scored)} ideas")

    elif version == "v2.1":
        from idea_pipeline.scoring import score_vault as score_vault_v21
        console.print("[dim]Running v2.1 scoring...[/dim]")
        result = score_vault_v21(vault_path, dry_run=dry_run, top_n=top_n, trigger=trigger)
    else:
        console.print(f"[red]Unknown version: {version}. Use v1 or v2.1[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Leaderboard ({version}{'  dry-run' if dry_run else ''})")
    table.add_column("#", style="dim")
    table.add_column("Idea")
    table.add_column("Score", justify="right")
    for rank, (idea_id, score) in enumerate(result.scored, 1):
        table.add_row(str(rank), idea_id, f"{score:.3f}")
    console.print(table)
    console.print(f"\n[bold]{len(result.scored)} ideas scored[/bold]")


def _run_cascade(new_idea_ids: list[str], vault_path: Path) -> None:
    """Auto-advance newly created ideas through research tiers T1→T4 based on rank.

    For each new idea:
      - T1: always researched (no rank gate)
      - T2-T4: researched only if the idea's rank is within the tier limit after re-scoring
    """
    import subprocess

    from idea_pipeline.research.web import resolve_tier_limit

    if not new_idea_ids:
        return

    console.print(f"\n[bold cyan]Cascade:[/bold cyan] advancing {len(new_idea_ids)} new idea(s) through T1→T4\n")

    # T1: research all new ideas unconditionally
    console.print(f"  [bold]T1[/bold] — researching {len(new_idea_ids)} idea(s)…")
    ids_arg = ",".join(new_idea_ids)
    result = subprocess.run(
        [sys.executable, "-m", "idea_pipeline.cli", "research", "--vault", str(vault_path), "--tier", "1", "--include", ids_arg],
        capture_output=False,
    )
    if result.returncode != 0:
        console.print(f"  [red]✗ T1 research failed (exit {result.returncode})[/red]")
        return

    # For T2-T4: re-score after each tier, then check rank vs limit
    eligible_ids = list(new_idea_ids)  # ideas still eligible to advance

    for tier in [2, 3, 4]:
        # Re-score vault to get current rankings
        score_result = score_vault(vault_path, dry_run=False)
        scored_ids = [idea_id for idea_id, _ in score_result.scored]
        vault_size = len(scored_ids)
        tier_limit = resolve_tier_limit(tier, vault_size, None)

        # Determine which eligible ideas are within the tier limit
        advanced: list[str] = []
        stopped: list[str] = []
        for idea_id in eligible_ids:
            if idea_id in scored_ids:
                rank = scored_ids.index(idea_id) + 1  # 1-based
            else:
                rank = vault_size + 1  # unscored → outside limit
            if rank <= tier_limit:
                advanced.append(idea_id)
                console.print(
                    f"  [green]↑[/green] {idea_id} advanced to T{tier} (rank #{rank}, limit={tier_limit})"
                )
            else:
                stopped.append(idea_id)
                console.print(
                    f"  [dim]–[/dim] {idea_id} stopped at T{tier - 1} (rank #{rank}, limit={tier_limit})"
                )

        if not advanced:
            console.print(f"  [dim]No ideas advanced to T{tier} — cascade complete.[/dim]")
            return

        # Research only the advanced ideas at this tier
        ids_arg = ",".join(advanced)
        console.print(f"  [bold]T{tier}[/bold] — researching {len(advanced)} idea(s)…")
        result = subprocess.run(
            [sys.executable, "-m", "idea_pipeline.cli", "research", "--vault", str(vault_path), "--tier", str(tier), "--include", ids_arg],
            capture_output=False,
        )
        if result.returncode != 0:
            console.print(f"  [red]✗ T{tier} research failed (exit {result.returncode})[/red]")
            return

        eligible_ids = advanced  # only advanced ideas remain eligible for next tier

    # Final re-score after T4
    score_result = score_vault(vault_path, dry_run=False)
    scored_ids = [idea_id for idea_id, _ in score_result.scored]
    vault_size = len(scored_ids)
    tier_limit = resolve_tier_limit(4, vault_size, None)
    for idea_id in eligible_ids:
        rank = (scored_ids.index(idea_id) + 1) if idea_id in scored_ids else vault_size + 1
        console.print(
            f"  [green]✓[/green] {idea_id} completed T4 (rank #{rank})"
        )

    console.print("\n[bold cyan]Cascade complete.[/bold cyan]")


def _auto_commit_and_push(tier: int, n_researched: int, project_root: Path, vault_path: Path) -> None:
    """Generate tier leaderboard, git add, commit, and push."""
    import subprocess

    leaderboard_path = project_root / f"LEADERBOARD_T{tier}.md"
    console.print(f"\n[bold]Auto-push:[/bold] generating {leaderboard_path.name} ...")

    # Generate the leaderboard via subprocess using the same Python interpreter
    report_args = [sys.executable, "-m", "idea_pipeline", "report",
                   f"--min-tier={tier}", f"--out=LEADERBOARD_T{tier}.md"]
    if tier >= 4:
        report_args.append("--include-candidates")
    gen_result = subprocess.run(
        report_args,
        capture_output=True,
        text=True,
        cwd=str(project_root),
    )
    if gen_result.returncode != 0:
        console.print(f"[yellow]⚠ Could not generate leaderboard: {gen_result.stderr.strip()}[/yellow]")
    else:
        console.print(f"  [green]✓[/green] {leaderboard_path.name} written")

    vault_dir = vault_path

    add_result = subprocess.run(
        ["git", "add", str(leaderboard_path), str(vault_dir)],
        capture_output=True,
        text=True,
        cwd=str(project_root),
    )
    if add_result.returncode != 0:
        console.print(f"[yellow]⚠ git add warning: {add_result.stderr.strip()}[/yellow]")

    commit_result = subprocess.run(
        ["git", "commit", "-m", f"research: T{tier} run — {n_researched} ideas researched"],
        capture_output=True,
        text=True,
        cwd=str(project_root),
    )
    if commit_result.returncode != 0:
        console.print(f"[yellow]⚠ git commit: {commit_result.stdout.strip() or commit_result.stderr.strip()}[/yellow]")
    else:
        console.print(f"  [green]✓[/green] committed: research: T{tier} run — {n_researched} ideas researched")

    push_result = subprocess.run(
        ["git", "push"],
        capture_output=True,
        text=True,
        cwd=str(project_root),
    )
    if push_result.returncode != 0:
        console.print(f"[yellow]⚠ git push failed: {push_result.stderr.strip()}[/yellow]")
    else:
        console.print(f"  [green]✓[/green] pushed to remote")


@app.command("research")
def research_cmd(
    vault: Optional[Path] = _vault_option,
    tier: int = typer.Option(1, "--tier", "-t", help="1=Tavily  2=Claude+WebSearch  3=Perplexity  4=Firecrawl  5=AutoResearch"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="Max ideas to process (default: tier default)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without calling APIs or writing"),
    force: bool = typer.Option(False, "--force", help="Re-run even if idea already has this tier or higher"),
    exclude: Optional[str] = typer.Option(None, "--exclude", help="Comma-separated idea IDs to skip"),
    include: Optional[str] = typer.Option(None, "--include", help="Comma-separated idea IDs to force-add (appended after top-N)"),
    no_auto_push: bool = typer.Option(False, "--no-auto-push", help="Skip automatic leaderboard generation and git push after run"),
) -> None:
    """Enrich ideas with external market research.

    T1=Tavily (all 142), T2=Claude+WebSearch (top 50), T3=Perplexity (top 10),
    T4=Firecrawl full-scrape (top 5), T5=AutoResearch 3-loop (top 5).
    Idempotent: skips ideas already at this tier or higher unless --force.
    """
    import datetime

    from idea_pipeline.research.web import MIN_CREDITS, TIER_LIMITS, get_researcher, resolve_tier_limit, tier_level

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    if tier not in range(1, 6):
        console.print(f"[red]✗ --tier must be 1–5 (got {tier})[/red]")
        raise typer.Exit(1)

    # T5 cost warning
    if tier == 5 and not dry_run:
        console.print(
            "[bold yellow]⚠ T5 ist token-intensiv (~$2–5 pro Idee).[/bold yellow]"
        )
        if not typer.confirm("Fortfahren?", default=False):
            raise typer.Exit(0)

    if dry_run:
        console.print("[bold yellow]Dry run[/bold yellow] — no APIs called, no files written.\n")

    exclude_ids = {e.strip() for e in exclude.split(",")} if exclude else set()
    include_ids = [e.strip() for e in include.split(",")] if include else []

    score_result = score_vault(vault_path, dry_run=True)
    scores_by_id = dict(score_result.scored)
    vault_size = len(scores_by_id)
    effective_limit = resolve_tier_limit(tier, vault_size, limit)
    console.print(f"T{tier} limit: {effective_limit} ideas (vault: {vault_size} ideas)")
    top_ideas = [(idea_id, sc) for idea_id, sc in score_result.scored if idea_id not in exclude_ids][:effective_limit]
    # Force-add included ideas not already in top_ideas
    top_idea_ids = {idea_id for idea_id, _ in top_ideas}
    for inc_id in include_ids:
        if inc_id not in top_idea_ids and inc_id in scores_by_id:
            top_ideas.append((inc_id, scores_by_id[inc_id]))

    from idea_pipeline.schemas import IdeeNote
    from idea_pipeline.vault_io import list_notes as _list_notes
    from idea_pipeline.vault_io import write_note as _write_note

    ideen_by_id = {n.model.id: n for n in _list_notes(vault_path, IdeeNote).notes}

    if dry_run:
        console.print(f"Would research up to {len(top_ideas)} ideas at tier {tier}:")
        skipped = 0
        for idea_id, score in top_ideas:
            vnote = ideen_by_id.get(idea_id)
            current = tier_level(vnote.model.research_fidelity if vnote else None)
            skip = not force and current >= tier
            label = f"[dim](skip — already {vnote.model.research_fidelity})[/dim]" if skip else ""
            skipped += skip
            console.print(f"  [cyan]{idea_id}[/cyan] (score={score:.3f}) {label}")
        stats = cache_stats()
        console.print(f"\n[dim]{skipped} would be skipped · Cache: {stats['total']} entries[/dim]")
        return

    try:
        researcher = get_researcher(tier)
    except (RuntimeError, ValueError) as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

    fidelity = f"tier{tier}"
    written = 0
    skipped = 0
    errors = 0
    report_entries: list[dict] = []
    t5_runs_dir = Path(__file__).resolve().parent.parent.parent / "runs"

    for rank, (idea_id, score) in enumerate(top_ideas, 1):
        vnote = ideen_by_id.get(idea_id)
        if vnote is None:
            continue
        idea = vnote.model

        # Idempotency: skip if already at this tier or higher
        if not force and tier_level(idea.research_fidelity) >= tier:
            console.print(
                f"  [{rank}/{len(top_ideas)}] [dim]{idea_id} — skip "
                f"({idea.research_fidelity})[/dim]"
            )
            skipped += 1
            continue

        # T5: require tier4 to be done first
        if tier == 5 and tier_level(idea.research_fidelity) < 4:
            console.print(
                f"  [{rank}/{len(top_ideas)}] [yellow]{idea_id} — skip "
                f"(T5 requires tier4 first)[/yellow]"
            )
            skipped += 1
            continue

        # Credit gate for T4 (Firecrawl)
        if tier == 4:
            remaining = researcher.remaining_credits()
            if remaining < MIN_CREDITS:
                console.print(
                    f"\n[yellow]⚠ Only {remaining} Firecrawl credits left — stopping early.[/yellow]"
                )
                break
            console.print(
                f"  [{rank}/{len(top_ideas)}] [cyan]{idea_id}[/cyan] "
                f"[dim](credits: {remaining})[/dim] ...",
                end=" ",
            )
        else:
            console.print(
                f"  [{rank}/{len(top_ideas)}] [cyan]{idea_id}[/cyan] ...", end=" "
            )

        try:
            if tier == 5:
                existing_ctx = idea.research_notes or ""
                scores, research_notes, _ = researcher.research_idea(
                    idea_id, idea.description or idea_id, existing_ctx
                )
            else:
                result = researcher.research_idea(idea_id, idea.description or idea_id)
                scores, narrative = result if isinstance(result, tuple) else (result, "")
                research_notes = ""
        except Exception as e:
            console.print(f"[red]error: {e}[/red]")
            errors += 1
            continue

        if tier == 5:
            if not research_notes:
                console.print("[dim]no data[/dim]")
                continue
            idea.research_notes = research_notes
            idea.research_fidelity = fidelity
            _write_note(vnote)
            written += 1
            console.print("[green]✓[/green]")
            # Save per-idea markdown
            t5_runs_dir.mkdir(exist_ok=True)
            today = datetime.date.today().isoformat()
            (t5_runs_dir / f"t5_{idea_id}_{today}.md").write_text(
                research_notes, encoding="utf-8"
            )
        else:
            if not scores:
                console.print("[dim]no data[/dim]")
                continue
            for field, val in scores.items():
                setattr(idea, field, val)
            idea.research_fidelity = fidelity
            _write_note(vnote)
            written += 1
            console.print(f"[green]✓[/green] {scores}")

            if tier >= 2 and narrative:
                wissen_links = idea.wissen or []
                report_entries.append({
                    "rank": rank,
                    "idea_id": idea_id,
                    "score": score,
                    "scores": scores,
                    "narrative": narrative,
                    "description": idea.description or "",
                    "wissen": [
                        str(w).strip("[]").replace("[[", "").replace("]]", "")
                        for w in wissen_links
                    ],
                })

    if tier != 5:
        console.print("\nRe-scoring vault ...")
        score_vault(vault_path)

    # Write narrative review report (T2–T4)
    if report_entries:
        reports_dir = Path(__file__).resolve().parent.parent.parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        today = datetime.date.today().isoformat()
        report_path = reports_dir / f"t{tier}_review_{today}.md"
        lines = [
            f"# T{tier} Market Research Review — {today}\n",
            f"Generated by `ideapipe research --tier {tier} --limit {effective_limit}`\n",
            "---\n",
        ]
        for e in report_entries:
            sc = e["scores"]
            lines += [
                f"## #{e['rank']} — {e['idea_id']}  (pipeline score: {e['score']:.3f})\n",
                f"**Knowledge areas:** {', '.join(e['wissen']) or '—'}\n",
                f"**Scores (1=best, 6=worst):** "
                f"market_size={sc.get('market_size','—')}  "
                f"market_potential={sc.get('market_potential','—')}  "
                f"prevalence={sc.get('prevalence','—')}  "
                f"market_awareness={sc.get('market_awareness','—')}\n",
                f"**Research findings:**\n{e['narrative']}\n",
                f"> *Description:* {e['description'][:300]}\n",
                "\n---\n",
            ]
        report_path.write_text("\n".join(lines), encoding="utf-8")
        console.print(f"\n[bold]Review report:[/bold] {report_path}")

    # Auto-push: generate tier leaderboard + commit + push (T2+ only, not dry-run, not suppressed)
    if not dry_run and not no_auto_push and tier >= 2 and written > 0:
        project_root = Path(__file__).resolve().parent.parent.parent
        _auto_commit_and_push(tier, written, project_root, vault_path)

    stats = cache_stats()
    console.print(
        f"\nDone. {written} enriched · {skipped} skipped · {errors} errors · "
        f"cache: {stats['total']} entries"
    )


@app.command("report")
def report_cmd(
    vault: Optional[Path] = _vault_option,
    out: Path = typer.Option(Path("LEADERBOARD.md"), "--out", "-o", help="Output markdown file"),
    version: str = typer.Option("v2.1", "--version", help="v1 or v2.1 column layout"),
    min_tier: Optional[int] = typer.Option(None, "--min-tier", help="Only include ideas at this research tier or higher (1–5)"),
    ids: Optional[str] = typer.Option(None, "--ids", help="Comma-separated idea IDs to include (all others excluded)"),
    include_candidates: bool = typer.Option(False, "--include-candidates", help="Also include (min_tier-1) ideas selected as candidates, marked as such"),
) -> None:
    """Write a ranked markdown leaderboard of all scored ideas."""
    import datetime
    import yaml

    from idea_pipeline.research.web import resolve_tier_limit, tier_level

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    ids_set = {i.strip() for i in ids.split(",")} if ids else set()

    # Determine candidate floor: when --include-candidates is active and min_tier >= 2,
    # also load ideas one tier below so candidates without target-tier data are shown.
    candidate_floor = (max(1, min_tier - 1) if (include_candidates and min_tier is not None and min_tier >= 2)
                       else min_tier)

    all_idea_rows: list[dict] = []
    vault_size = 0
    for f in vault_path.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        try:
            meta = yaml.safe_load(parts[1]) or {}
        except Exception:
            continue
        db = meta.get("database") or []
        if isinstance(db, str):
            db = [db]
        if not any("geschaeftsideen" in str(d) for d in db):
            continue
        vault_size += 1
        if meta.get("score") is None:
            continue
        if candidate_floor is not None and tier_level(meta.get("research_fidelity")) < candidate_floor:
            continue
        if ids_set and f.stem not in ids_set:
            continue
        all_idea_rows.append({"id": f.stem, **meta})

    all_idea_rows.sort(key=lambda x: x.get("score", 0), reverse=True)

    # When include_candidates: keep only the top-N that would have been selected for min_tier research.
    if include_candidates and min_tier is not None and min_tier >= 2:
        pool_limit = resolve_tier_limit(min_tier, vault_size, None)
        ideas = all_idea_rows[:pool_limit]
    else:
        ideas = all_idea_rows

    ideas.sort(key=lambda x: x.get("score", 0), reverse=True)

    def fmt(v, fallback="—"):
        return str(v) if v is not None else fallback

    def mastery_bar(v):
        if v is None:
            return "·"
        v = float(v)
        if v >= 0.75:
            return "▇"
        if v >= 0.50:
            return "▅"
        if v >= 0.25:
            return "▃"
        return "▁"

    today = datetime.date.today().isoformat()

    if version == "v2.1":
        lines = [
            f"# Idea Leaderboard v2.1 — {today}",
            "",
            f"**{len(ideas)} ideas scored** · generated by `ideapipe report --version v2.1`",
            "",
            "| # | Idea | Score | Res | Cap | Reg | Kill | Mast | Obs | CD | WTP | mkt↑ | fit↑ | ch↑ | att↑ |",
            "|---|------|------:|:---:|:---:|:---:|:----:|:----:|:---:|:--:|:---:|:----:|:----:|:---:|:----:|",
        ]
        tier_badge_map = {"tier1": "T1", "tier2": "T2", "tier3": "T3", "tier4": "T4✓", "tier5": "T5✓"}
        for rank, m in enumerate(ideas, 1):
            sb = m.get("score_breakdown") or {}
            cd = "✓" if sb.get("cross_domain_flag") else "·"
            kill = "💀" if sb.get("killer_flag") else "·"
            idea_tier = tier_level(m.get("research_fidelity"))
            # Mark candidates selected for target tier but without new data
            if include_candidates and min_tier is not None and idea_tier < min_tier:
                res = f"T{idea_tier}→"
            else:
                res = tier_badge_map.get(m.get("research_fidelity") or "", "—")
            lines.append(
                f"| {rank} "
                f"| {m['id']} "
                f"| {m.get('score', 0):.3f} "
                f"| {res} "
                f"| {fmt(sb.get('capital_class'), '—')[:4]} "
                f"| {fmt(sb.get('regulation_class'), '—')[:4]} "
                f"| {kill} "
                f"| {mastery_bar(sb.get('mastery_leverage'))} "
                f"| {mastery_bar(sb.get('obsession_leverage'))} "
                f"| {cd} "
                f"| {fmt(sb.get('willingness_to_pay'))} "
                f"| {sb.get('market_score', 0):.1f} "
                f"| {sb.get('fit_score', 0):.1f} "
                f"| {sb.get('chance_score', 0):.1f} "
                f"| {sb.get('attractiveness_score', 0):.1f} |"
            )
        lines += [
            "",
            "---",
            "",
            "**Column guide**",
            "- **Res**: research tier (T1–T5; T4✓/T5✓ = Firecrawl/AutoResearch data present; T3→ = selected as T4 candidate, no T4 data yet)",
            "- **Cap**: capital_class (boot=bootstrappable, seed=seed, vc=vc_dependent)",
            "- **Reg**: regulation_class (un=unregulated, lo=low, hi=high)",
            "- **Kill**: 💀 = killer_flag (vc_dependent + high regulation)",
            "- **Mast/Obs**: mastery/obsession leverage ▁▃▅▇ (0.0→1.0)",
            "- **CD**: cross_domain_flag ✓ = true",
            "- **WTP**: willingness_to_pay (1=high, 6=low)",
            "",
        ]
    else:
        # v1 layout (existing behavior)
        def tier_badge(t):
            return {"tier1": "T1", "tier2": "T2", "tier3": "T3", "tier4": "T4", "tier5": "T5"}.get(t or "", "—")

        def wissen_str(meta):
            links = meta.get("wissen") or []
            if isinstance(links, str):
                links = [links]
            names = [str(w).strip("[]").replace("[[", "").replace("]]", "") for w in links]
            return ", ".join(names) if names else "—"

        lines = [
            f"# Idea Leaderboard — {today}",
            "",
            f"**{len(ideas)} ideas scored** · generated by `ideapipe report`",
            "",
            "| # | Idea | Score | Tier | mSz | mPot | prev | mAw | ch↑ | ws↑ | intr↑ | Wissen |",
            "|---|------|------:|:----:|:---:|:----:|:----:|:---:|:---:|:---:|:-----:|--------|",
        ]
        for rank, m in enumerate(ideas, 1):
            sb = m.get("score_breakdown") or {}
            lines.append(
                f"| {rank} | {m['id']} | {m.get('score', 0):.3f} "
                f"| {tier_badge(m.get('research_fidelity'))} "
                f"| {fmt(m.get('market_size'))} | {fmt(m.get('market_potential'))} "
                f"| {fmt(m.get('prevalence'))} | {fmt(m.get('market_awareness'))} "
                f"| {sb.get('chance_score', 0):.1f} | {sb.get('wissen_score', 0):.1f} "
                f"| {sb.get('intrinsic_score', 0):.1f} | {wissen_str(m)} |"
            )

    if not out.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent.parent
        out = repo_root / out

    out.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]✓[/green] {out}  ({len(ideas)} ideas)")


@app.command("compare-versions")
def compare_versions_cmd(
    vault: Optional[Path] = _vault_option,
) -> None:
    """Compare v1 vs v2.1 scores — show rank movements."""
    import datetime

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    from idea_pipeline.vault_io import list_notes
    from idea_pipeline.schemas import IdeeNote

    ideas = list_notes(vault_path, IdeeNote).notes
    rows = []
    for vnote in ideas:
        idea = vnote.model
        if idea.score is None or idea.score_v1 is None:
            continue
        rows.append({
            "id": idea.id,
            "score_v1": idea.score_v1,
            "score_v21": idea.score,
        })

    v1_sorted = sorted(rows, key=lambda x: x["score_v1"], reverse=True)
    v21_sorted = sorted(rows, key=lambda x: x["score_v21"], reverse=True)
    v1_rank = {r["id"]: i + 1 for i, r in enumerate(v1_sorted)}
    v21_rank = {r["id"]: i + 1 for i, r in enumerate(v21_sorted)}

    for r in rows:
        r["v1_rank"] = v1_rank[r["id"]]
        r["v21_rank"] = v21_rank[r["id"]]
        r["delta"] = r["v1_rank"] - r["v21_rank"]

    rows.sort(key=lambda x: abs(x["delta"]), reverse=True)

    today = datetime.date.today().isoformat()
    lines = [
        f"# v1 vs v2.1 Score Comparison — {today}",
        "",
        f"**{len(rows)} ideas compared**",
        "",
        "| Idea | v1 Score | v1 Rank | v2.1 Score | v2.1 Rank | Δ Rank |",
        "|------|:--------:|:-------:|:----------:|:---------:|:------:|",
    ]
    for r in rows:
        delta_str = f"+{r['delta']}" if r["delta"] > 0 else str(r["delta"])
        lines.append(
            f"| {r['id']} "
            f"| {r['score_v1']:.3f} "
            f"| #{r['v1_rank']} "
            f"| {r['score_v21']:.3f} "
            f"| #{r['v21_rank']} "
            f"| {delta_str} |"
        )

    lines += ["", "---", ""]
    lines += ["## Top 10 Aufsteiger (v1→v2.1)"]
    risers = sorted(rows, key=lambda x: x["delta"], reverse=True)[:10]
    for r in risers:
        lines.append(f"- **{r['id']}**: #{r['v1_rank']} → #{r['v21_rank']} (+{r['delta']})")

    lines += ["", "## Top 10 Absteiger (v1→v2.1)"]
    fallers = sorted(rows, key=lambda x: x["delta"])[:10]
    for r in fallers:
        lines.append(f"- **{r['id']}**: #{r['v1_rank']} → #{r['v21_rank']} ({r['delta']})")

    reports_dir = Path(__file__).resolve().parent.parent.parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    out = reports_dir / f"v1_vs_v2_1_comparison_{today}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]✓[/green] {out}")


@app.command("progression")
def progression_cmd(
    vault: Optional[Path] = _vault_option,
    idea_id: Optional[str] = typer.Option(None, "--idea-id", help="Show history for one idea"),
    all_ideas: bool = typer.Option(False, "--all", help="Show all ideas with score history"),
    top: int = typer.Option(20, "--top", help="With --all: show top N ideas"),
) -> None:
    """Show score progression over time from score_history."""
    from idea_pipeline.vault_io import list_notes
    from idea_pipeline.schemas import IdeeNote

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    ideas = list_notes(vault_path, IdeeNote).notes

    if idea_id:
        match = next((n for n in ideas if n.model.id == idea_id), None)
        if not match:
            console.print(f"[red]Idea not found:[/red] {idea_id}")
            raise typer.Exit(1)
        history = match.model.score_history
        if not history:
            console.print(f"No score history for {idea_id}")
            raise typer.Exit(0)
        table = Table(title=f"Score history: {idea_id}")
        table.add_column("Date")
        table.add_column("Version")
        table.add_column("Score", justify="right")
        table.add_column("Rank", justify="right")
        table.add_column("Trigger")
        for entry in history:
            table.add_row(
                entry.date,
                entry.version,
                f"{entry.score:.3f}",
                str(entry.rank or "—"),
                entry.trigger or "—",
            )
        console.print(table)
    elif all_ideas:
        rows = [n for n in ideas if n.model.score_history]
        rows.sort(key=lambda n: n.model.score or 0, reverse=True)
        rows = rows[:top]

        table = Table(title=f"Score progression — top {top}")
        table.add_column("Idea")
        all_versions: list[str] = []
        for n in rows:
            for e in n.model.score_history:
                if e.version not in all_versions:
                    all_versions.append(e.version)

        for v in all_versions:
            table.add_column(f"Score ({v})", justify="right")
            table.add_column(f"Rank ({v})", justify="right")

        for n in rows:
            hist_by_version = {}
            for e in n.model.score_history:
                hist_by_version[e.version] = e
            row_data = [n.model.id]
            for v in all_versions:
                e = hist_by_version.get(v)
                row_data.append(f"{e.score:.3f}" if e else "—")
                row_data.append(f"#{e.rank}" if e and e.rank else "—")
            table.add_row(*row_data)

        console.print(table)
    else:
        console.print("Specify --idea-id X or --all")


@app.command("enrich-intrinsic")
def enrich_intrinsic_cmd(
    vault: Optional[Path] = _vault_option,
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without API calls"),
    force: bool = typer.Option(False, "--force", help="Re-enrich already-enriched ideas"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="Process only N ideas"),
) -> None:
    """Step 10: LLM batch rebuild of attractiveness, fit, and gates for all ideas.

    Idempotent: skips ideas already enriched (attractiveness_impact != 6), unless --force.
    Costs ~$5 for all 142 ideas (Sonnet 4.6, batch size 5).
    """
    import math

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    from idea_pipeline.vault_io import list_notes
    from idea_pipeline.schemas import IdeeNote

    all_ideen = list_notes(vault_path, IdeeNote).notes
    to_enrich = [n for n in all_ideen if n.model.attractiveness_impact == 6 or force]
    effective = min(len(to_enrich), limit or len(to_enrich))

    estimated_cost = (math.ceil(effective / 5)) * 0.15
    console.print(f"[bold]enrich-intrinsic[/bold]  {effective} ideas · ~${estimated_cost:.2f} estimated")

    if not dry_run and estimated_cost > 1.0:
        confirm = typer.confirm(f"Run LLM enrichment for {effective} ideas (~${estimated_cost:.2f})?")
        if not confirm:
            console.print("Aborted.")
            raise typer.Exit(0)

    result = run_intrinsic_enrich(
        vault_path,
        dry_run=dry_run,
        force=force,
        limit=limit,
    )
    console.print(
        f"\n[green]✓[/green] enriched={len(result.enriched)} "
        f"skipped={len(result.skipped)} "
        f"errors={len(result.errors)}"
    )
    if result.errors:
        for idea_id, msg in result.errors:
            console.print(f"  [red]✗[/red] {idea_id}: {msg}")


@app.command("generate")
def generate_cmd(
    vault: Optional[Path] = _vault_option,
    domain: Optional[str] = typer.Option(None, "--domain", "-d", help="Domain to analyze, e.g. 'myzel leder'"),
    from_vault: bool = typer.Option(False, "--from-vault", help="Auto-select high-market/low-fit vault ideas"),
    limit: int = typer.Option(5, "--limit", "-n", help="Max vault ideas to process (Path B only)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Research + analyze but don't write to vault"),
    select: Optional[str] = typer.Option(None, "--select", help="Non-interactive selection, e.g. '1,3'"),
    cascade: bool = typer.Option(True, "--cascade/--no-cascade", help="Auto-advance new ideas through T1→T4 research tiers"),
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

    if dry_run and cascade:
        console.print(f"\n[dim]Would cascade new idea(s) from {len(domains)} domain(s) through tiers T1→T4 (--cascade)[/dim]")

    if not dry_run and all_written:
        console.print(f"\n[green]✓[/green] {len(all_written)} new idea(s) written to vault.")
        if cascade:
            _run_cascade(all_written, vault_path)
        else:
            console.print("Run [bold]ideapipe score --version v2.1[/bold] to score them.")


@app.command("select-hypotheses")
def select_hypotheses(
    vault: Optional[Path] = _vault_option,
    n: int = typer.Option(8, "--n", help="Number of hypotheses to select"),
    min_tier: int = typer.Option(4, "--min-tier", help="Minimum research fidelity tier (1-5)"),
    out: Path = typer.Option(Path("HYPOTHESES.md"), "--out", "-o", help="Output markdown file"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without calling APIs or writing"),
) -> None:
    """Select 5-10 diverse hypotheses from the top-scored ideas using domain diversity.

    Classifies each idea into a domain using Claude Haiku, then selects
    one idea per domain (greedy diversity) to produce a rich HYPOTHESES.md.
    """
    import datetime
    import json

    import anthropic

    from idea_pipeline.research.sources.base import tier_level
    from idea_pipeline.vault_io import list_notes as _list_notes

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    # --- 1. Load ideas with research_fidelity >= min_tier, sorted by score desc ---
    all_notes = _list_notes(vault_path, IdeeNote).notes
    eligible: list[IdeeNote] = [
        vn.model for vn in all_notes
        if vn.model.score is not None and tier_level(vn.model.research_fidelity) >= min_tier
    ]
    eligible.sort(key=lambda m: m.score or 0, reverse=True)

    if not eligible:
        console.print(f"[yellow]No ideas with research_fidelity >= tier{min_tier} found.[/yellow]")
        raise typer.Exit(0)

    console.print(f"[bold]select-hypotheses[/bold]: {len(eligible)} eligible ideas (tier{min_tier}+), selecting {n} diverse picks\n")

    # --- Dry run: print preview and return without calling any API ---
    if dry_run:
        console.print(f"[yellow]Dry run[/yellow] — no APIs called, no files written.\n")
        console.print(f"select-hypotheses: {len(eligible)} eligible ideas (tier{min_tier}+)")
        console.print(f"Would call Claude Haiku to classify into domains, then select {n} diverse picks.")
        console.print("Top eligible idea IDs (would-select preview):")
        for i, idea in enumerate(eligible[:n], 1):
            console.print(f"  [cyan]{i}.[/cyan] [bold]{idea.id}[/bold]  score={idea.score:.4f}  tier={idea.research_fidelity}")
        console.print("\nPass without --dry-run to generate HYPOTHESES.md")
        return

    # --- 2. Classify into domains using Claude Haiku batch calls ---
    DOMAINS = [
        "B2B_SaaS", "B2C_App", "Marketplace", "DeepTech", "Sustainability",
        "BioTech", "AgriTech", "FinTech", "EdTech", "HealthTech",
        "Hardware", "Services", "Other",
    ]
    domain_cache: dict[str, str] = {}

    haiku_client = anthropic.Anthropic()

    def classify_batch(batch: list[IdeeNote]) -> dict[str, str]:
        """Send up to 10 ideas to Haiku, get {id: domain} back."""
        ideas_payload = {
            m.id: (m.description or m.id)[:300]
            for m in batch
        }
        prompt = (
            "Given these idea titles/descriptions, classify each into exactly one of: "
            + ", ".join(DOMAINS)
            + ".\nReturn ONLY valid JSON in the format: {\"idea_id\": \"domain\", ...}\n\n"
            + "Ideas:\n"
            + json.dumps(ideas_payload, ensure_ascii=False)
        )
        try:
            resp = haiku_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            data = json.loads(text)
            # Validate domains
            result = {}
            for idea_id, domain in data.items():
                result[idea_id] = domain if domain in DOMAINS else "Other"
            return result
        except Exception as e:
            console.print(f"[yellow]Haiku classification error: {e} — falling back to 'Other'[/yellow]")
            return {m.id: "Other" for m in batch}

    console.print("Classifying ideas into domains via Claude Haiku...")
    batch_size = 10
    for i in range(0, len(eligible), batch_size):
        batch = eligible[i:i + batch_size]
        # Skip already cached
        uncached = [m for m in batch if m.id not in domain_cache]
        if uncached:
            classifications = classify_batch(uncached)
            domain_cache.update(classifications)
        for m in batch:
            if m.id not in domain_cache:
                domain_cache[m.id] = "Other"

    # --- 3. Greedy diversity selection ---
    selected: list[IdeeNote] = []
    seen_domains: set[str] = set()

    for idea in eligible:
        domain = domain_cache.get(idea.id, "Other")
        if domain not in seen_domains:
            selected.append(idea)
            seen_domains.add(domain)
            if len(selected) >= n:
                break

    # --- 4. Fallback: allow repeats if not enough unique domains ---
    if len(selected) < n:
        remaining = [m for m in eligible if m not in selected]
        for idea in remaining:
            selected.append(idea)
            if len(selected) >= n:
                break

    # --- Dry run: just print selected IDs and domains ---
    if dry_run:
        console.print(f"\n[bold]Would select {len(selected)} hypotheses:[/bold]\n")
        for i, idea in enumerate(selected, 1):
            domain = domain_cache.get(idea.id, "Other")
            console.print(f"  [cyan]{i}.[/cyan] [bold]{idea.id}[/bold]  domain=[yellow]{domain}[/yellow]  score={idea.score:.4f}  tier={idea.research_fidelity}")
        return

    # --- 5. Load chance + wissen notes for descriptions ---
    chance_notes_by_id = {
        vn.model.id: vn.model
        for vn in _list_notes(vault_path, ChanceNote).notes
    }
    wissen_notes_by_id = {
        vn.model.id: vn.model
        for vn in _list_notes(vault_path, WissenNote).notes
    }

    # --- 6. Pull cached narratives (T2=claude_search, T3=perplexity, T4=firecrawl) ---
    def get_cached_narrative(idea_id: str, fidelity: str | None) -> str:
        """Try to retrieve the research narrative from the SQLite cache."""
        try:
            from idea_pipeline.research.cache import cache_get

            source_map = {
                "tier2": ("claude_search_v1", f"t2:{idea_id}"),
                "tier3": ("perplexity_v1", f"t3:{idea_id}"),
                "tier4": ("firecrawl_v2", f"t4:{idea_id}"),
            }
            tier_key = fidelity or ""
            if tier_key not in source_map:
                return ""
            source, query = source_map[tier_key]
            result = cache_get(query, source)
            if result:
                return result.get("narrative", "")
            return ""
        except Exception:
            return ""

    def get_all_narratives(idea: IdeeNote) -> str:
        """Concatenate all available research narratives for an idea."""
        parts = []
        fidelity = idea.research_fidelity or ""
        tier_n = tier_level(fidelity)

        # T2 narrative
        if tier_n >= 2:
            n2 = get_cached_narrative(idea.id, "tier2")
            if n2:
                parts.append(f"**T2 (Claude Web Search):** {n2}")

        # T3 narrative
        if tier_n >= 3:
            n3 = get_cached_narrative(idea.id, "tier3")
            if n3:
                parts.append(f"**T3 (Perplexity Sonar):** {n3}")

        # T4 narrative — pull from T4 review report file as fallback
        if tier_n >= 4:
            n4 = get_cached_narrative(idea.id, "tier4")
            if not n4:
                # Parse from report file
                try:
                    repo_root = Path(__file__).resolve().parent.parent.parent
                    reports_dir = repo_root / "reports"
                    for rfile in sorted(reports_dir.glob("t4_review_*.md"), reverse=True):
                        text = rfile.read_text(encoding="utf-8")
                        # Find section for this idea
                        marker = f"## #"
                        sections = text.split(marker)
                        for sec in sections[1:]:
                            if idea.id in sec[:80]:
                                rf_start = sec.find("**Research findings:**\n")
                                if rf_start >= 0:
                                    rf_text = sec[rf_start + len("**Research findings:**\n"):]
                                    rf_text = rf_text.split("\n>")[0].strip()
                                    n4 = rf_text
                                break
                        if n4:
                            break
                except Exception:
                    pass
            if n4:
                parts.append(f"**T4 (Firecrawl Deep Research):** {n4}")

        # T5 research_notes
        if idea.research_notes:
            parts.append(f"**T5 (AutoResearch):** {idea.research_notes}")

        return "\n\n".join(parts) if parts else "_No research narrative available yet._"

    def fmt_score_breakdown(sb: dict | None) -> str:
        if not sb:
            return "_No breakdown available._"
        lines = []
        if "market_score" in sb:
            lines.append(f"Market attractiveness: {sb['market_score']:.2f}/6")
        if "fit_score" in sb:
            lines.append(f"Founder fit: {sb['fit_score']:.2f}/6")
        if "chance_score" in sb:
            lines.append(f"Problem strength: {sb['chance_score']:.2f}/6")
        if "attractiveness_score" in sb:
            lines.append(f"Idea attractiveness: {sb['attractiveness_score']:.2f}/6")
        if "willingness_to_pay" in sb:
            lines.append(f"Willingness to pay: {sb['willingness_to_pay']}/6 (1=high)")
        if sb.get("killer_flag"):
            lines.append("⚠ Killer flag: vc_dependent + high regulation — high execution risk")
        return " | ".join(lines) if lines else "_No breakdown_"

    def recommended_path(capital_class: str | None) -> str:
        if capital_class == "bootstrappable":
            return "Self-fund → validate MVP → grow organically"
        elif capital_class == "seed":
            return "Pre-seed angels → MVP → seed round"
        elif capital_class == "vc_dependent":
            return "Validate core hypothesis deeply before fundraising; needs institutional capital"
        return "Assess capital needs before committing to a path"

    # --- 7. Generate HYPOTHESES.md ---
    today = datetime.date.today().isoformat()
    lines: list[str] = [
        "# Idea Pipeline — Working Hypotheses",
        f"Generated: {today} | Source: T{min_tier}+ leaderboard | Selected: {len(selected)} diverse ideas",
        "",
        "---",
        "",
    ]

    for i, idea in enumerate(selected, 1):
        domain = domain_cache.get(idea.id, "Other")
        sb = idea.score_breakdown or {}

        # Linked problems
        chance_lines: list[str] = []
        for cid in (idea.chancen or []):
            c = chance_notes_by_id.get(cid)
            desc = (c.description or "").strip() if c else ""
            chance_lines.append(f"- **{cid}**: {desc[:120]}" if desc else f"- {cid}")

        # Linked wissen
        wissen_lines: list[str] = []
        for wid in (idea.wissen or []):
            wissen_lines.append(f"- {wid}")

        # First adopters
        fa_str = ", ".join(idea.first_adopters) if idea.first_adopters else "TBD"

        narratives = get_all_narratives(idea)

        lines += [
            f"## Hypothesis #{i}: {idea.id}",
            f"**Score:** {idea.score:.4f} | **Tier:** T{tier_level(idea.research_fidelity)} | **Domain:** {domain}",
            f"**Capital:** {idea.capital_class or '—'} | **Regulation:** {idea.regulation_class or '—'}",
            "",
            "### Why this hypothesis?",
            fmt_score_breakdown(sb),
            "",
            "### What we know (Research Findings)",
            narratives,
            "",
            "### Linked Problems",
            "\n".join(chance_lines) if chance_lines else "_No linked problems._",
            "",
            "### Founder Fit",
            f"Mastery: {idea.mastery_leverage:.0%} | Obsession: {idea.obsession_leverage:.0%} | Cross-domain: {idea.cross_domain_flag}",
            f"Relevant knowledge: {', '.join(idea.wissen) if idea.wissen else '—'}",
            "",
            "### Recommended Next Steps",
            "- [ ] Autoresearch (T5): deepen counter-arguments and competitor analysis",
            "- [ ] Expert interview: identify 3 domain experts via LinkedIn/network",
            f"- [ ] Customer discovery: 5 conversations with {fa_str}",
            f"- [ ] Build/buy/partner decision: {idea.capital_class or '—'} → {recommended_path(idea.capital_class)}",
            "",
            "---",
            "",
        ]

    # Resolve output path relative to repo root if not absolute
    if not out.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent.parent
        out = repo_root / out

    out.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"\n[green]✓[/green] {len(selected)} hypotheses written to [bold]{out}[/bold]")
    console.print("\n[bold]Selected:[/bold]")
    for i, idea in enumerate(selected, 1):
        domain = domain_cache.get(idea.id, "Other")
        console.print(f"  {i}. [cyan]{idea.id}[/cyan]  [{domain}]  score={idea.score:.4f}")


@app.command("full-report")
def full_report_cmd(
    vault: Optional[Path] = _vault_option,
    min_tier: int = typer.Option(4, "--min-tier"),
    limit: int = typer.Option(25, "--limit"),
    out: Path = typer.Option(Path("FULL_REPORT.md"), "--out", "-o"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview first 3 ideas, no file written"),
) -> None:
    """Generate a detailed per-idea report for top T4+ ideas.

    Includes score breakdown, all research narratives (T2/T3/T4),
    linked problem fields, knowledge areas, and idea notes.
    """
    from idea_pipeline.research.sources.base import tier_level
    from idea_pipeline.report import build_full_report

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    # Load candidates: ideas at (min_tier - 1) or higher, up to limit.
    # This includes ideas selected for T{min_tier} research that didn't yield new data.
    candidate_floor = max(1, min_tier - 1)
    all_notes = list_notes(vault_path, IdeeNote).notes
    eligible: list[IdeeNote] = [
        vn.model for vn in all_notes
        if vn.model.score is not None and tier_level(vn.model.research_fidelity) >= candidate_floor
    ]
    eligible.sort(key=lambda m: m.score or 0, reverse=True)
    ideas = eligible[:limit]

    total_eligible = len(eligible)
    n_with_data = sum(1 for i in ideas if tier_level(i.research_fidelity) >= min_tier)
    n_candidates = len(ideas) - n_with_data

    if dry_run:
        console.print(
            f"[bold yellow]Dry run[/bold yellow] — "
            f"{total_eligible} ideas at tier{candidate_floor}+, would include {len(ideas)} (limit={limit}): "
            f"{n_with_data} with T{min_tier} data, {n_candidates} candidates without.\n"
            f"Preview of first 3:\n"
        )
        for rank, idea in enumerate(ideas[:3], 1):
            sb = idea.score_breakdown or {}
            no_data = " [yellow](no T{} data)[/yellow]".format(min_tier) if tier_level(idea.research_fidelity) < min_tier else ""
            console.print(
                f"  [bold]#{rank}[/bold] [cyan]{idea.id}[/cyan]{no_data}  "
                f"score={idea.score:.3f}  tier={idea.research_fidelity}  "
                f"capital={idea.capital_class or '—'}  "
                f"wtp={idea.willingness_to_pay}/6"
            )
            console.print(
                f"       market={sb.get('market_score', 0):.2f}  "
                f"fit={sb.get('fit_score', 0):.2f}  "
                f"chance={sb.get('chance_score', 0):.2f}  "
                f"attr={sb.get('attractiveness_score', 0):.2f}"
            )
        return

    if not ideas:
        console.print(f"[yellow]No ideas found at tier{candidate_floor}+ with a score.[/yellow]")
        raise typer.Exit(0)

    console.print(
        f"Building full report for [bold]{len(ideas)}[/bold] ideas "
        f"({n_with_data} with T{min_tier} data, {n_candidates} candidates without) ..."
    )

    report_text = build_full_report(ideas, vault_path, target_tier=min_tier)

    if not out.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent.parent
        out = repo_root / out

    out.write_text(report_text, encoding="utf-8")
    console.print(f"[green]✓[/green] {out}  ({len(ideas)} ideas)")


if __name__ == "__main__":
    app()
