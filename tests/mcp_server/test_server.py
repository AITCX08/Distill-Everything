"""Protocol-level MCP tests over the SDK's in-memory transport."""

from __future__ import annotations

import asyncio
import io

import pytest

pytest.importorskip("mcp")

from mcp.shared.memory import create_connected_server_and_client_session as connect

from src.mcp_server.server import _StdoutShim, build_server
from tests.mcp_server.test_tools import FakeService

EXPECTED_TOOLS = {
    "distill_list_platforms",
    "distill_preview",
    "distill_create_job",
    "distill_list_jobs",
    "distill_get_job",
    "distill_list_job_items",
    "distill_list_artifacts",
    "distill_read_artifact",
    "distill_control_job",
}


def test_server_exposes_the_agent_surface():
    server = build_server(FakeService())

    async def scenario():
        async with connect(server) as session:
            listed = await session.list_tools()
            return {tool.name for tool in listed.tools}

    assert asyncio.run(scenario()) == EXPECTED_TOOLS


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


def test_stdout_shim_keeps_the_binary_protocol_stream():
    original = io.TextIOWrapper(io.BytesIO())
    errors = io.StringIO()

    shim = _StdoutShim(original, errors)
    shim.write("downloading 42%")
    shim.flush()

    assert shim.buffer is original.buffer
    assert errors.getvalue() == "downloading 42%"
