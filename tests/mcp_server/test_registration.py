"""Registration tests: run with any MCP SDK version that can build a server."""

from __future__ import annotations

import asyncio
import io

import pytest

pytest.importorskip("mcp")

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


def test_server_registers_the_agent_surface_with_descriptions():
    server = build_server(FakeService())

    listed = asyncio.run(server.list_tools())
    tools = getattr(listed, "tools", listed)  # newer SDK wraps it in ListToolsResult

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert all(tool.description for tool in tools), "每个工具都必须有描述，Agent 才能选对"


def test_stdout_shim_keeps_the_binary_protocol_stream():
    original = io.TextIOWrapper(io.BytesIO())
    errors = io.StringIO()

    shim = _StdoutShim(original, errors)
    shim.write("downloading 42%")
    shim.flush()

    assert shim.buffer is original.buffer
    assert errors.getvalue() == "downloading 42%"
