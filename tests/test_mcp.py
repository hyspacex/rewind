from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from rewind.store import initialize


async def _list_and_call(root: Path) -> tuple[set[str], object, object]:
    project_root = Path(__file__).parents[1]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "rewind.mcp_server"],
        cwd=str(root),
        env={**os.environ, "PYTHONPATH": str(project_root)},
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("get_status", {})
            run_tool = next(tool for tool in tools.tools if tool.name == "run_check")
            return {tool.name for tool in tools.tools}, result.structuredContent, run_tool.inputSchema


def test_mcp_lists_exact_cli_parity_tools_and_calls_status(git_repo: Path) -> None:
    initialize(git_repo)
    names, status, run_schema = asyncio.run(_list_and_call(git_repo))
    assert names == {"start_task", "get_status", "checkpoint", "run_check", "finish_task"}
    assert status == {"active": False, "last_task_id": None}
    assert "action_id" in run_schema["properties"]
    assert "action_id" not in run_schema.get("required", [])
