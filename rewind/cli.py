"""Rewind command-line interface."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .audit import (
    activate_policy,
    approve_action,
    change_role,
    observe_deployment,
    record_artifact,
)
from .demo import create_demo
from .git_state import checkpoint_integrity_issue, create_recovery_branch
from .inspect import recorded_evidence, task_diff
from .receipt import build_receipt
from .replay import render_replay_text, replay_action
from .report import render_report
from .store import RewindError, RewindPaths, git_root, initialize
from .task import (
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
audit_app = typer.Typer(
    no_args_is_help=True,
    help="Record the small human-operated lifecycle used by historical replay.",
)
app.add_typer(audit_app, name="audit")
console = Console()


def paths() -> RewindPaths:
    return RewindPaths(git_root(Path.cwd()))


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"


def receipt_signal(receipt: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((signal for signal in receipt["signals"] if signal["key"] == key), None)


def terminal_safe(value: object) -> str:
    """Make recorded output visible without replaying terminal control characters."""
    text = str(value)
    safe: list[str] = []
    for character in text:
        codepoint = ord(character)
        if character in {"\n", "\t"} or character.isprintable():
            safe.append(character)
        elif codepoint <= 0xFF:
            safe.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            safe.append(f"\\u{codepoint:04x}")
        else:
            safe.append(f"\\U{codepoint:08x}")
    return "".join(safe)


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
            "Policy v1 is active. Private state is ignored by Git.\n"
            "Review and commit .rewind/config.json and .rewind/.gitignore.",
            title="REWIND",
        )
    )


@app.command("approve")
def approve_command(action_id: str = typer.Argument(...)) -> None:
    """Record a human approval for an existing protected action."""
    try:
        event = approve_action(paths(), action_id)
    except RewindError as exc:
        fail(exc)
    console.print(f"[green]Approval recorded[/green] {event['content_id'][:12]} for {action_id}")


@audit_app.command("policy")
def audit_policy_command(version: str = typer.Argument(..., help="Policy version: v1 or v2.")) -> None:
    """Activate a packaged historical-replay policy."""
    try:
        event = activate_policy(paths(), version)
    except RewindError as exc:
        fail(exc)
    console.print(
        f"[green]Policy {version} activated[/green] {event['content_id'][:12]}"
    )


@audit_app.command("role-grant")
def audit_role_grant_command(
    subject: str = typer.Argument(...),
    role: str = typer.Argument(...),
) -> None:
    """Record a human role grant."""
    try:
        event = change_role(paths(), subject, role, grant=True)
    except RewindError as exc:
        fail(exc)
    console.print(
        f"[green]Role granted[/green] {role} → {subject} · {event['content_id'][:12]}"
    )


@audit_app.command("role-revoke")
def audit_role_revoke_command(
    subject: str = typer.Argument(...),
    role: str = typer.Argument(...),
) -> None:
    """Record a human role revocation."""
    try:
        event = change_role(paths(), subject, role, grant=False)
    except RewindError as exc:
        fail(exc)
    console.print(
        f"[yellow]Role revoked[/yellow] {role} → {subject} · {event['content_id'][:12]}"
    )


@audit_app.command("artifact")
def audit_artifact_command(
    action_id: str = typer.Argument(...),
    artifact: Path = typer.Argument(..., help="Artifact file to hash and record."),
    proposer: str = typer.Option(..., "--proposer", help="Actor proposing the artifact."),
) -> None:
    """Bind an artifact to passing evidence for a protected action."""
    try:
        event = record_artifact(paths(), action_id, artifact, proposer)
    except (RewindError, OSError) as exc:
        fail(RewindError(str(exc)))
    digest = str(event["payload"]["artifact_sha256"])
    console.print(
        f"[green]Artifact recorded[/green] {digest[:12]} for {action_id} · "
        f"event {event['content_id'][:12]}"
    )


@audit_app.command("approve")
def audit_approve_command(
    action_id: str = typer.Argument(...),
    approver: str = typer.Option("human", "--approver", help="Human approval identity."),
) -> None:
    """Record a human approval for a protected action."""
    try:
        event = approve_action(paths(), action_id, approver)
    except RewindError as exc:
        fail(exc)
    console.print(
        f"[green]Approval recorded[/green] {event['content_id'][:12]} for {action_id}"
    )


@audit_app.command("deployment")
def audit_deployment_command(
    action_id: str = typer.Argument(...),
    actor: str = typer.Option(..., "--actor", help="Actor that performed the external deployment."),
) -> None:
    """Record that an external deployment was observed."""
    try:
        event = observe_deployment(paths(), action_id, actor)
    except RewindError as exc:
        fail(exc)
    console.print(
        f"[green]Deployment observed[/green] {action_id} by {actor} · "
        f"{event['content_id'][:12]}"
    )


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
    local_paths = paths()
    if not local_paths.local.exists():
        console.print(
            Panel.fit(
                "[bold yellow]Rewind is not initialized in this repository.[/bold yellow]\n"
                "Run: rewind init",
                title="REWIND",
            )
        )
        return
    if not local_paths.private_key.exists() or not local_paths.state.exists():
        console.print(
            Panel.fit(
                "[bold yellow]Local Rewind recorder state is not initialized.[/bold yellow]\n"
                "Run: rewind init\n"
                "Existing .rewind/config.json and .rewind/.gitignore will be preserved.",
                title="REWIND",
            )
        )
        return
    try:
        result = status(local_paths)
    except RewindError as exc:
        fail(exc)
    if not result["active"]:
        console.print("[dim]No active Rewind task.[/dim]")
        if result.get("last_task_id"):
            try:
                last_receipt = build_receipt(local_paths, str(result["last_task_id"]))
            except RewindError as exc:
                fail(exc)
            evidence = receipt_signal(last_receipt, "evidence")
            table = Table(title="REWIND / LAST TASK", show_header=False)
            table.add_row("Task", str(result["last_task_id"]))
            table.add_row("Outcome", str(last_receipt["outcome"]))
            if evidence:
                table.add_row("Evidence", f"{evidence['status']} — {evidence['summary']}")
            console.print(table)
        return
    try:
        active_receipt = build_receipt(local_paths, str(result["task_id"]))
    except RewindError as exc:
        fail(exc)
    evidence = receipt_signal(active_receipt, "evidence")
    drift_count = len(active_receipt["changed_after_passing_evidence"])
    table = Table(title="REWIND / ACTIVE TASK", show_header=False)
    table.add_row("Task", str(result["task_id"]))
    table.add_row("Intent", str(result["intent"]))
    table.add_row("Scope", "\n".join(result["allowed_paths"]))
    table.add_row("Checkpoints", str(result["checkpoint_count"]))
    table.add_row("Recorded checks", str(result["check_count"]))
    if evidence:
        table.add_row("Evidence", f"{evidence['status']} — {evidence['summary']}")
    table.add_row(
        "Recorded post-check drift",
        "None" if not drift_count else plural(drift_count, "file"),
    )
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
def run_command(
    ctx: typer.Context,
    action_id: str | None = typer.Option(
        None,
        "--action",
        help="Bind this check to a protected audit action ID.",
    ),
) -> None:
    """Run and record a check: rewind run -- COMMAND [ARGS...]."""
    argv = list(ctx.args)
    try:
        result = run_check(
            paths(),
            argv,
            **({"action_id": action_id} if action_id is not None else {}),
        )
    except RewindError as exc:
        fail(exc)
    state = "[green]PASS[/green]" if result["passed"] else "[red]FAIL[/red]"
    action_copy = f" · action {action_id}" if action_id else ""
    console.print(
        f"\nREWIND recorded check {state} · {result['checkpoint_id']} · "
        f"{result['duration_ms']} ms · evidence {str(result['evidence_sha256'])[:12]}"
        f"{action_copy}"
    )
    if not result["passed"]:
        raise typer.Exit(int(result["exit_code"]) or 1)


@app.command("finish")
def finish_command() -> None:
    """Record the final checkpoint and close the active task."""
    local_paths = paths()
    try:
        result = finish_task(local_paths)
        receipt = build_receipt(local_paths, result["task_id"])
    except RewindError as exc:
        fail(exc)
    evidence = receipt_signal(receipt, "evidence")
    evidence_copy = (
        f"{evidence['status']} — {evidence['summary']}"
        if evidence
        else "Unavailable"
    )
    console.print(
        Panel.fit(
            f"[bold green]Task finished[/bold green]  {result['task_id']}\n"
            f"Final state: {result['checkpoint']['checkpoint_id']}\n"
            f"Outcome: {receipt['outcome']}\n"
            f"Evidence: {evidence_copy}\n"
            f"Review: {plural(int(receipt['review_item_count']), 'item')}",
            title="REWIND",
        )
    )


def render_receipt_table(receipt: dict[str, Any]) -> None:
    console.print()
    console.print("[bold]REWIND / TASK RECEIPT[/bold]")
    console.print(f"[bold]{receipt['intent']}[/bold]\n")
    outcome_color = {"pass": "green", "warn": "yellow", "fail": "red"}[str(receipt["outcome_key"])]
    review_count = int(receipt["review_item_count"])
    display_outcome = (
        "Completed"
        if receipt["outcome"] == "Completed with items to review"
        else str(receipt["outcome"])
    )
    console.print(
        f"[bold {outcome_color}]{display_outcome}[/bold {outcome_color}]"
        f" · {plural(review_count, 'item')} to review\n"
    )
    table = Table(show_header=False, box=None, padding=(0, 2))
    for signal in receipt["signals"]:
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
        f"{plural(int(receipt['test_count']), 'test')} passed"
        if receipt.get("test_count") is not None
        else f"{plural(int(receipt['passing_check_count']), 'passing check')}"
    )
    recovery = receipt.get("recommended_recovery")
    console.print(
        f"{plural(len(receipt['changed_files']), 'file')} changed · {test_copy}\n"
        f"Recovery state: {recovery['checkpoint_id'] if recovery else 'none'} · "
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


@app.command("evidence")
def evidence_command(
    task_id: str | None = typer.Argument(None, help="Task ID; defaults to active or latest task."),
    digest: str | None = typer.Option(
        None,
        "--digest",
        help="Evidence SHA-256 or unique prefix; defaults to the latest check.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the verified evidence object as JSON."),
) -> None:
    """Inspect hash-verified stdout and stderr for a recorded check."""
    try:
        record = recorded_evidence(paths(), task_id, digest)
    except RewindError as exc:
        fail(exc)

    if as_json:
        console.print(
            Text(json.dumps(record, indent=2, ensure_ascii=True)),
            soft_wrap=True,
        )
        return

    evidence = record["output"]
    argv = evidence.get("argv", [])
    command = shlex.join(str(part) for part in argv) if isinstance(argv, list) else str(argv)
    table = Table(title="REWIND / VERIFIED EVIDENCE", show_header=False)
    table.add_row("Task", Text(str(record["task_id"])))
    table.add_row("Checkpoint", Text(str(record["checkpoint_id"])))
    table.add_row("Kind", Text(str(record["kind"])))
    table.add_row("Command", Text(terminal_safe(command)))
    table.add_row("Exit", Text(str(evidence.get("exit_code"))))
    table.add_row("Duration", f"{evidence.get('duration_ms')} ms")
    table.add_row("SHA-256", Text(str(record["evidence_sha256"])[:12]))
    console.print(table)
    console.print(
        Text(f"Evidence digest: {record['evidence_sha256']}"),
        soft_wrap=True,
    )

    for label, value in (("STDOUT", evidence.get("stdout", "")), ("STDERR", evidence.get("stderr", ""))):
        safe_output = terminal_safe(value)
        console.print(
            Panel(
                Text(safe_output if safe_output else "(empty)"),
                title=f"REWIND / {label}",
            )
        )


@app.command("diff")
def diff_command(
    task_id: str | None = typer.Argument(None, help="Task ID; defaults to active or latest task."),
    stat: bool = typer.Option(False, "--stat", help="Show a summary instead of the full binary-safe patch."),
) -> None:
    """Show the exact recorded Git diff for a task."""
    try:
        output = task_diff(paths(), task_id, stat=stat)
    except RewindError as exc:
        fail(exc)
    if not output:
        console.print("[dim]No recorded changes.[/dim]")
        return
    safe_output = terminal_safe(output)
    console.print(
        Text(safe_output),
        soft_wrap=True,
        end="" if safe_output.endswith("\n") else "\n",
    )


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
    task_id: str | None = typer.Option(
        None,
        "--task",
        help="Task ID containing the checkpoint; defaults to active or latest.",
    ),
    branch: str = typer.Option(..., "--branch", help="New branch name; must start with rewind/."),
) -> None:
    """Create a branch at a checkpoint without switching or modifying current work."""
    local_paths = paths()
    try:
        selected_task_id = find_task(local_paths, task_id)
        checkpoint_events = [
            event
            for event in task_events(local_paths, selected_task_id)
            if event["type"] == "checkpoint_recorded"
            and event["payload"]["checkpoint_id"] == checkpoint_id
        ]
        if not checkpoint_events:
            raise RewindError(
                f"Checkpoint {checkpoint_id} was not found in task {selected_task_id}."
            )
        if len(checkpoint_events) > 1:
            raise RewindError(
                f"Checkpoint {checkpoint_id} is ambiguous in task {selected_task_id}; "
                "recovery was not created."
            )
        checkpoint_event = checkpoint_events[0]
        checkpoint_payload = checkpoint_event["payload"]
        integrity_issue = checkpoint_integrity_issue(
            local_paths.root,
            commit=str(checkpoint_payload["commit"]),
            tree=str(checkpoint_payload["tree"]),
            ref=str(checkpoint_payload["ref"]),
        )
        if integrity_issue:
            raise RewindError(
                f"Checkpoint {checkpoint_id} failed integrity verification: {integrity_issue}."
            )
        create_recovery_branch(local_paths.root, branch, checkpoint_payload["commit"])
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
