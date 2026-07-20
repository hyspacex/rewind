"""Thin MCP adapter. Task tools are registered after the task core is installed."""

from __future__ import annotations


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("rewind")

    @server.tool()
    def get_status() -> str:
        """Return the current Rewind integration status."""
        return "Rewind bootstrap is installed; task tools are being activated."

    server.run(transport="stdio")


if __name__ == "__main__":
    main()

