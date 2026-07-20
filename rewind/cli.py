"""Rewind command-line interface."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from . import __version__
from .store import RewindError, git_root, initialize

app = typer.Typer(
    no_args_is_help=True,
    help="Rewind — a flight recorder for coding agents.",
    rich_markup_mode="rich",
)
console = Console()


def version_callback(value: bool) -> None:
    if value:
        console.print(f"rewind {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", callback=version_callback, is_eager=True),
) -> None:
    """Record agent work, bind checks to Git state, and recover safely."""


@app.command("init")
def init_command() -> None:
    """Initialize the signed recorder in the current Git repository."""
    try:
        paths = initialize(git_root(Path.cwd()))
    except RewindError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        Panel.fit(
            f"[bold green]Recorder initialized[/bold green]\n{paths.local}\n"
            "Policy v1 is active. Private state is ignored by Git.",
            title="REWIND",
        )
    )


if __name__ == "__main__":
    app()

