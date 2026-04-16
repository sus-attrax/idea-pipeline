"""CLI entry point for the idea pipeline.

Each command is one isolated, idempotent pipeline step.
This is the contract that you (and later, Claude via Claude Code) use
to drive the system.

Commands grow with each pipeline step:
  Step 1: hello, info
  Step 2: schema check, schema check-dir
  ...
"""

from __future__ import annotations

from pathlib import Path

import frontmatter
import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from idea_pipeline.schemas import detect_note_type

app = typer.Typer(
    name="ideapipe",
    help="Business idea validation & generation pipeline.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

schema_app = typer.Typer(help="Schema validation utilities.", no_args_is_help=True)
app.add_typer(schema_app, name="schema")


@app.command()
def hello(name: str = "Meister") -> None:
    """Smoke test — proves the CLI is wired up correctly."""
    console.print(f"[bold green]✓[/bold green] Pipeline-Skeleton lebt. Hallo, {name}.")


@app.command()
def info() -> None:
    """Show pipeline status and configured paths."""
    from idea_pipeline import __version__

    console.print(f"[bold]idea-pipeline[/bold] v{__version__}")
    console.print("[dim]Vault path noch nicht konfiguriert (Step 4).[/dim]")


def _validate_one(file: Path) -> tuple[str, object | None, str | None]:
    """Validate one note. Returns (status, model_or_None, error_msg_or_None).

    Status values: 'valid', 'invalid', 'unknown_type'
    """
    try:
        post = frontmatter.load(file)
    except Exception as e:
        return ("invalid", None, f"frontmatter parse failed: {e}")

    schema_cls = detect_note_type(post.metadata)
    if schema_cls is None:
        return ("unknown_type", None, None)

    try:
        data = dict(post.metadata)
        data["id"] = file.stem  # ID derived from filename, never from YAML
        note = schema_cls.model_validate(data)
        return ("valid", note, None)
    except ValidationError as e:
        # First error message is usually the most useful
        first_err = e.errors()[0]
        loc = ".".join(str(p) for p in first_err["loc"])
        return ("invalid", None, f"{loc}: {first_err['msg']}")


@schema_app.command("check")
def schema_check(
    file: Path = typer.Argument(..., help="Path to a markdown note file"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show parsed model"),
) -> None:
    """Validate a single markdown note against its appropriate schema.

    The schema class is auto-detected from the note's `database` field
    in the YAML frontmatter:
      database: [[geschaeftsideen🎲]] → IdeeNote
      database: [[chancen🌋]]          → ChanceNote
      database: [[wissen🤯]]           → WissenNote
    """
    if not file.exists():
        console.print(f"[red]✗ File not found:[/red] {file}")
        raise typer.Exit(1)

    status, note, err = _validate_one(file)

    if status == "unknown_type":
        console.print(
            f"[yellow]?[/yellow] {file.name}: cannot detect type "
            f"(no recognized [bold]database[/bold] field)"
        )
        raise typer.Exit(2)
    if status == "invalid":
        console.print(f"[red]✗[/red] {file.name}: {err}")
        raise typer.Exit(1)

    cls_name = type(note).__name__
    console.print(f"[bold green]✓[/bold green] {file.name} valid as [bold]{cls_name}[/bold]")

    if verbose:
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Field", style="dim")
        table.add_column("Value")
        for field_name, field_value in note.model_dump().items():
            if field_value is None or field_value == [] or field_value == "":
                continue
            # Truncate long descriptions for readability
            val_str = str(field_value)
            if len(val_str) > 100:
                val_str = val_str[:97] + "..."
            table.add_row(field_name, val_str)
        console.print(table)


@schema_app.command("check-dir")
def schema_check_dir(
    directory: Path = typer.Argument(..., help="Directory containing markdown notes"),
    show_unknown: bool = typer.Option(
        False, "--show-unknown", help="List files with no detectable type"
    ),
) -> None:
    """Validate all .md notes in a directory (flat structure).

    Reports valid / invalid / unknown_type counts.
    Exit code is non-zero if any file is invalid (good for CI).
    """
    if not directory.is_dir():
        console.print(f"[red]✗ Not a directory:[/red] {directory}")
        raise typer.Exit(1)

    files = sorted(directory.glob("*.md"))
    if not files:
        console.print(f"[yellow]No .md files found in {directory}[/yellow]")
        raise typer.Exit(0)

    valid = 0
    invalid_list: list[tuple[str, str]] = []
    unknown_list: list[str] = []
    counts_by_type: dict[str, int] = {}

    for f in files:
        status, note, err = _validate_one(f)
        if status == "valid":
            valid += 1
            cls_name = type(note).__name__
            counts_by_type[cls_name] = counts_by_type.get(cls_name, 0) + 1
        elif status == "invalid":
            invalid_list.append((f.name, err or "unknown error"))
        else:
            unknown_list.append(f.name)

    # Summary
    console.print(
        f"\n[bold]Summary[/bold] across {len(files)} files: "
        f"[green]{valid} valid[/green] · "
        f"[red]{len(invalid_list)} invalid[/red] · "
        f"[dim]{len(unknown_list)} unknown type[/dim]"
    )
    if counts_by_type:
        breakdown = " · ".join(f"{k}: {v}" for k, v in sorted(counts_by_type.items()))
        console.print(f"[dim]Breakdown: {breakdown}[/dim]")

    if invalid_list:
        console.print("\n[bold red]Invalid:[/bold red]")
        for filename, msg in invalid_list:
            console.print(f"  [red]✗[/red] {filename}: {msg}")

    if show_unknown and unknown_list:
        console.print("\n[bold yellow]Unknown type (skipped):[/bold yellow]")
        for filename in unknown_list:
            console.print(f"  [yellow]?[/yellow] {filename}")

    if invalid_list:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
