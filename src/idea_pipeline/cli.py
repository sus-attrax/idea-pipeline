"""CLI entry point for the idea pipeline.

Each command is one isolated, idempotent pipeline step.
This is the contract that you (and later, Claude via MCP) use to drive the system.

Why typer + rich?
- typer auto-generates --help from type hints, no boilerplate
- rich gives us nice colored output for free
- both are battle-tested, low-magic libraries
"""

import typer
from rich.console import Console

app = typer.Typer(
    name="ideapipe",
    help="Business idea validation & generation pipeline.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def hello(name: str = "Meister") -> None:
    """Smoke test — proves the CLI is wired up correctly."""
    console.print(f"[bold green]✓[/bold green] Pipeline-Skeleton lebt. Hallo, {name}.")
    console.print("[dim]Nächster Step: Pydantic-Schemas für idee/chance/wissen.[/dim]")


@app.command()
def info() -> None:
    """Show pipeline status and configured paths."""
    from idea_pipeline import __version__

    console.print(f"[bold]idea-pipeline[/bold] v{__version__}")
    console.print("[dim]Konfiguration noch nicht implementiert (Step 4).[/dim]")


if __name__ == "__main__":
    app()
