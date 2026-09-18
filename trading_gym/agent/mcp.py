"""Optional MCP adapter over the curated SafeToolRegistry.

The import is intentionally optional. Deployments without the MCP dependency can
still use the same bounded tool registry through the REST API.
"""

from __future__ import annotations

from trading_gym.agent.tools import SafeToolRegistry, ToolRequest


def create_mcp_server(registry: SafeToolRegistry | None = None):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - optional integration
        raise RuntimeError(
            "Install the optional 'mcp' package to expose MCP tools"
        ) from exc

    registry = registry or SafeToolRegistry()
    server = FastMCP("trading-gym-safe-tools")

    @server.tool()
    def list_tools() -> list[dict[str, str]]:
        """List safe tools available to an agent."""
        return registry.describe()

    @server.tool()
    def invoke_tool(tool: str, arguments: dict, actor: str = "mcp-agent"):
        """Invoke a curated tool. Trade-like operations only create proposals."""
        return registry.invoke(ToolRequest(tool=tool, arguments=arguments, actor=actor))

    return server
