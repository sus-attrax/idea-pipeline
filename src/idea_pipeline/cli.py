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
from idea_pipeline.link import LinkResult, run_link
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


if __name__ == "__main__":
    app()
