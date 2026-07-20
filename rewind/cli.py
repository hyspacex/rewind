"""Rewind command-line interface."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .demo import create_demo
from .git_state import create_recovery_branch
from .receipt import build_receipt
from .replay import render_replay_text, replay_action
from .report import render_report
from .store import RewindError, RewindPaths, git_root, initialize, project
from .task import (
    events,
    find_task,
    finish_task,
    record_checkpoint,
    run_check,
    start_task,
    status,
    task_events,
)

app = typer.Typer(
    no_args_is_help=True,
    help="Rewind — a flight recorder for coding agents.",
    rich_markup_mode="rich",
)
console = Console()


def paths() -> RewindPaths:
    return RewindPaths(git_root(Path.cwd()))


def fail(exc: RewindError) -> None:
    console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(1) from exc


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


@app.command("approve")
def approve_command(action_id: str = typer.Argument(...)) -> None:
    """Record a human approval for an existing protected action."""
    local_paths = paths()
    try:
        target = next(
            (
                event
                for event in reversed(events(local_paths))
                if event["payload"].get("action_id") == action_id
                and event["type"] in {"artifact_recorded", "deployment_observed"}
            ),
            None,
        )
        if target is None:
            raise RewindError(f"Unknown action ID: {action_id}")
        payload = target["payload"]
        event = project(local_paths).append(
            "approval_recorded",
            {
                "action_id": action_id,
                "tree": payload.get("tree"),
                "artifact_sha256": payload.get("artifact_sha256"),
                "approver": "human",
            },
            actor="human",
        )
    except RewindError as exc:
        fail(exc)
    console.print(f"[green]Approval recorded[/green] {event['content_id'][:12]} for {action_id}")


@app.command("replay")
def replay_command(action_id: str = typer.Argument(...)) -> None:
    """Replay an action under historical and current policy."""
    local_paths = paths()
    try:
        result = replay_action(
            local_paths.events,
            local_paths.public_key,
            local_paths.objects,
            action_id,
        )
    except (RewindError, OSError, ValueError) as exc:
        fail(RewindError(str(exc)))
    console.print(Panel(render_replay_text(result), title=f"REWIND / REPLAY / {action_id}"))


@app.command("demo")
def demo_command(
    output: Path = typer.Option(..., "--output", "-o", help="New or empty output directory."),
) -> None:
    """Generate the complete network-free judge demonstration."""
    try:
        result = create_demo(output)
    except (RewindError, OSError) as exc:
        fail(RewindError(str(exc)))
    console.print(
        Panel.fit(
            f"[bold green]Judge demo ready[/bold green]\n{result['output']}\n\n"
            f"Developer report: {result['task_report']}\n"
            f"Forensic report: {result['forensic_report']}\n"
            f"Recovery branch: {result['recovery_branch']}",
            title="REWIND / DEMO",
        )
    )


@app.command("start")
def start_command(
    intent: str = typer.Option(..., "--intent", help="The task the agent is allowed to perform."),
    allow: list[str] = typer.Option(..., "--allow", help="Allowed path glob; repeat for each scope."),
) -> None:
    """Start a scoped task and record its initial Git checkpoint."""
    try:
        result = start_task(paths(), intent, allow)
    except RewindError as exc:
        fail(exc)
    console.print(
        Panel.fit(
            f"[bold green]Task started[/bold green]  {result['task_id']}\n"
            f"{result['intent']}\nInitial state: {result['checkpoint']['checkpoint_id']}",
            title="REWIND",
        )
    )


@app.command("status")
def status_command() -> None:
    """Show the active task and latest recorded state."""
    try:
        result = status(paths())
    except RewindError as exc:
        fail(exc)
    if not result["active"]:
        console.print("[dim]No active Rewind task.[/dim]")
        if result.get("last_task_id"):
            console.print(f"Last task: {result['last_task_id']}")
        return
    table = Table(title="REWIND / ACTIVE TASK", show_header=False)
    table.add_row("Task", str(result["task_id"]))
    table.add_row("Intent", str(result["intent"]))
    table.add_row("Scope", "\n".join(result["allowed_paths"]))
    table.add_row("Checkpoints", str(result["checkpoint_count"]))
    table.add_row("Recorded checks", str(result["check_count"]))
    console.print(table)


@app.command("checkpoint")
def checkpoint_command(
    label: str = typer.Option(..., "--label", help="Meaningful checkpoint label."),
) -> None:
    """Snapshot the working tree under a private Git ref."""
    try:
        result = record_checkpoint(paths(), label)
    except RewindError as exc:
        fail(exc)
    console.print(
        f"[green]Recorded {result['checkpoint_id']}[/green] {result['tree'][:12]}  {result['label']}"
    )


@app.command(
    "run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run_command(ctx: typer.Context) -> None:
    """Run and record a check: rewind run -- COMMAND [ARGS...]."""
    argv = list(ctx.args)
    try:
        result = run_check(paths(), argv)
    except RewindError as exc:
        fail(exc)
    state = "[green]PASS[/green]" if result["passed"] else "[red]FAIL[/red]"
    console.print(
        f"\nREWIND recorded check {state} · {result['checkpoint_id']} · "
        f"{result['duration_ms']} ms · evidence {str(result['evidence_sha256'])[:12]}"
    )
    if not result["passed"]:
        raise typer.Exit(int(result["exit_code"]) or 1)


@app.command("finish")
def finish_command() -> None:
    """Record the final checkpoint and close the active task."""
    try:
        result = finish_task(paths())
    except RewindError as exc:
        fail(exc)
    console.print(
        Panel.fit(
            f"[bold green]Task finished[/bold green]  {result['task_id']}\n"
            f"Final state: {result['checkpoint']['checkpoint_id']}",
            title="REWIND",
        )
    )


def render_receipt_table(receipt: dict[str, object]) -> None:
    console.print()
    console.print("[bold]REWIND / TASK RECEIPT[/bold]")
    console.print(f"[bold]{receipt['intent']}[/bold]\n")
    outcome_color = {"pass": "green", "warn": "yellow", "fail": "red"}[str(receipt["outcome_key"])]
    console.print(
        f"[bold {outcome_color}]{receipt['outcome']}[/bold {outcome_color}]"
        f" with {receipt['review_item_count']} item(s) to review\n"
    )
    table = Table(show_header=False, box=None, padding=(0, 2))
    for signal in receipt["signals"]:  # type: ignore[union-attr]
        color = {"PASS": "green", "READY": "green", "WARN": "yellow", "FAIL": "red", "NONE": "red"}[
            signal["status"]
        ]
        table.add_row(
            f"[bold]{signal['label']}[/bold]",
            f"[{color}]{signal['status']}[/{color}]",
            str(signal["summary"]),
        )
    console.print(table)
    console.print()
    test_copy = (
        f"{receipt['test_count']} tests passed"
        if receipt.get("test_count") is not None
        else f"{receipt['passing_check_count']} passing checks"
    )
    recovery = receipt.get("recommended_recovery")
    console.print(
        f"{len(receipt['changed_files'])} files changed · {test_copy}\n"
        f"Safe state: {recovery['checkpoint_id'] if recovery else 'none'} · "
        f"Final state: {receipt['final_checkpoint']['checkpoint_id']}"
    )
    if receipt["outside_scope"]:
        console.print("\n[yellow]Outside declared scope:[/yellow]")
        for file in receipt["outside_scope"]:
            console.print(f"  {file}")
    if receipt["changed_after_passing_evidence"]:
        console.print("\n[yellow]Changed after the last passing check:[/yellow]")
        for file in receipt["changed_after_passing_evidence"]:
            console.print(f"  {file}")


@app.command("receipt")
def receipt_command(task_id: str | None = typer.Argument(None)) -> None:
    """Show the developer-facing task receipt."""
    try:
        receipt = build_receipt(paths(), task_id)
    except RewindError as exc:
        fail(exc)
    render_receipt_table(receipt)


@app.command("timeline")
def timeline_command(task_id: str | None = typer.Argument(None)) -> None:
    """Show the signed chronological task timeline."""
    try:
        selected = find_task(paths(), task_id)
        relevant = task_events(paths(), selected)
    except RewindError as exc:
        fail(exc)
    table = Table(title=f"REWIND / TIMELINE / {selected}")
    table.add_column("Seq", justify="right")
    table.add_column("Time")
    table.add_column("Actor")
    table.add_column("Event")
    table.add_column("Content ID")
    for event in relevant:
        table.add_row(
            str(event["sequence"]),
            event["recorded_at"],
            event["actor"],
            event["type"].replace("_", " "),
            event["content_id"][:12],
        )
    console.print(table)


@app.command("report")
def report_command(
    task_id: str | None = typer.Argument(None),
    output: Path = typer.Option(Path("rewind-report.html"), "--output", "-o"),
) -> None:
    """Generate a standalone, self-contained HTML task report."""
    try:
        receipt = build_receipt(paths(), task_id)
        destination = render_report(receipt, output)
    except (RewindError, OSError) as exc:
        fail(RewindError(str(exc)))
    console.print(f"[green]Report generated[/green] {destination}")


@app.command("recover")
def recover_command(
    checkpoint_id: str = typer.Argument(...),
    branch: str = typer.Option(..., "--branch", help="New branch name; must start with rewind/."),
) -> None:
    """Create a branch at a checkpoint without switching or modifying current work."""
    local_paths = paths()
    try:
        task_id = find_task(local_paths)
        checkpoint_event = next(
            (
                event
                for event in reversed(task_events(local_paths, task_id))
                if event["type"] == "checkpoint_recorded"
                and event["payload"]["checkpoint_id"] == checkpoint_id
            ),
            None,
        )
        if checkpoint_event is None:
            raise RewindError(f"Checkpoint {checkpoint_id} was not found in task {task_id}.")
        create_recovery_branch(local_paths.root, branch, checkpoint_event["payload"]["commit"])
    except RewindError as exc:
        fail(exc)
    console.print(
        Panel.fit(
            f"[bold green]Recovery branch created[/bold green]\n{branch} → "
            f"{checkpoint_event['payload']['commit'][:12]}\n\n"
            "Your branch, index, HEAD, and working tree were not changed.\n"
            f"When ready: git switch {branch}",
            title="REWIND / SAFE RECOVERY",
        )
    )


if __name__ == "__main__":
    app()
