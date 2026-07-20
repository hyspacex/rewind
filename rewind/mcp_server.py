"""Thin stdio MCP adapter over the authoritative task core."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .store import RewindPaths, git_root
from .task import (
    finish_task as core_finish_task,
    record_checkpoint,
    run_check as core_run_check,
    start_task as core_start_task,
    status,
)


def _paths() -> RewindPaths:
    return RewindPaths(git_root(Path.cwd()))


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("rewind")

    @server.tool(name="start_task")
    def start_task_tool(intent: str, allowed_paths: list[str]) -> dict[str, Any]:
        """Start a narrowly scoped Rewind task before meaningful work."""
        return core_start_task(_paths(), intent, allowed_paths)

    @server.tool()
    def get_status() -> dict[str, Any]:
        """Return the active Rewind task and latest recorded state."""
        return status(_paths())

    @server.tool()
    def checkpoint(label: str) -> dict[str, Any]:
        """Record a meaningful non-destructive Git checkpoint."""
        return record_checkpoint(_paths(), label)

    @server.tool()
    def run_check(argv: list[str]) -> dict[str, Any]:
        """Execute argv without a shell and bind its evidence to exact Git state."""
        return core_run_check(_paths(), argv)

    @server.tool(name="finish_task")
    def finish_task_tool() -> dict[str, Any]:
        """Record the final checkpoint and close the active task."""
        return core_finish_task(_paths())

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
