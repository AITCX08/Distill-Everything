"""Protocol-level MCP tests over the SDK's in-memory transport.

The in-memory helper is not present in every ``mcp`` release, so this module
skips cleanly instead of failing collection on older SDK builds.
"""

from __future__ import annotations

import asyncio

import pytest

from src.mcp_server.server import build_server
from tests.mcp_server.test_tools import FakeService

pytest.importorskip("mcp")
memory = pytest.importorskip("mcp.shared.memory")

connect = getattr(memory, "create_connected_server_and_client_session", None)
if connect is None:  # pragma: no cover - depends on the installed SDK
    pytest.skip(
        "installed mcp SDK has no in-memory transport helper",
        allow_module_level=True,
    )




def test_tool_call_round_trips_over_the_wire():
    server = build_server(FakeService())

    async def scenario():
        async with connect(server) as session:
            return await session.call_tool("distill_list_platforms", {})

    result = asyncio.run(scenario())

    assert result.isError is False
    assert "douyin" in result.content[0].text


def test_invalid_arguments_come_back_as_tool_errors():
    server = build_server(FakeService())

    async def scenario():
        async with connect(server) as session:
            return await session.call_tool("distill_preview", {"target": "  "})

    result = asyncio.run(scenario())

    assert result.isError is True
