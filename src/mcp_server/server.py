"""MCP server exposing the distillation pipeline to other AI agents.

Transports: ``stdio`` (Claude Desktop / Cursor), ``sse`` and ``streamable-http``
(local agents that prefer HTTP). Every tool is a thin wrapper over
``src.mcp_server.tools`` so the protocol layer contains no business logic.
"""

from __future__ import annotations

import sys
from typing import Any

from src.mcp_server import tools

INSTRUCTIONS = """Distill-Everything 是一个本地优先的内容蒸馏工具。

推荐调用顺序：
1. `distill_list_platforms` 看有哪些平台、登录状态如何；
2. `distill_preview` 预检目标链接（只枚举，不下载），拿到 `fingerprint`；
3. `distill_create_job` 用上一步的 fingerprint 创建并启动任务；
4. 轮询 `distill_get_job` / `distill_list_job_items` 看进度；
5. `distill_list_artifacts` + `distill_read_artifact` 读产物。

平台登录（Bilibili / 抖音）必须在本地 Dashboard 或 CLI 完成，MCP 不接收也不返回任何凭据。
产物是 Markdown / JSON 文本；绝对路径一律不外传，只给文件名。
"""


def _register(server: Any, name: str):
    """Register a tool by name, tolerating SDK builds without ``name=``."""

    def decorate(function: Any) -> Any:
        try:
            return server.tool(name=name)(function)
        except TypeError:  # older SDK: only the bare decorator exists
            return server.tool()(function)

    return decorate


class _StdoutShim:
    """Send application text output to stderr, keep stdout for the protocol.

    The MCP stdio transport wraps ``sys.stdout.buffer``, while ``rich`` and
    ``print`` write the *text* layer. Mirroring only the text layer keeps agent
    transcripts from being corrupted by progress output.
    """

    def __init__(self, original: Any, target: Any) -> None:
        self._original = original
        self._target = target

    @property
    def buffer(self) -> Any:
        return self._original.buffer

    def write(self, data: str) -> int:
        return self._target.write(data)

    def flush(self) -> None:
        self._target.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


def build_server(service: Any, *, host: str = "127.0.0.1", port: int = 8877) -> Any:
    """Build the FastMCP server; the SDK is imported lazily so the core stays light."""

    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "当前安装的是 MCP SDK 2.x（FastMCP 已改名 MCPServer），本项目适配 1.x；"
            "请执行 `pip install \"mcp<2\"` 后重试。"
        ) from error

    try:
        server = FastMCP(
            "distill-everything",
            instructions=INSTRUCTIONS,
            host=host,
            port=port,
        )
    except TypeError:  # older SDK builds without host/port kwargs
        server = FastMCP("distill-everything", instructions=INSTRUCTIONS)

    @_register(server, "distill_list_platforms")
    def distill_list_platforms() -> list[dict[str, Any]]:
        """列出可用的内容平台（bilibili / douyin / youtube）、支持的作品类型与登录状态。"""

        return tools.list_platforms(service)

    @_register(server, "distill_preview")
    def distill_preview(
        target: str,
        platform: str = "auto",
        outputs: list[str] | None = None,
        rag_chunks: bool = False,
    ) -> dict[str, Any]:
        """预检创作者主页、抖音收藏夹/合集或单视频链接：只解析并枚举，不下载、不写状态，返回 fingerprint。"""

        return tools.preview_target(
            service,
            target,
            platform=platform,
            outputs=outputs,
            rag_chunks=rag_chunks,
        )

    @_register(server, "distill_create_job")
    def distill_create_job(
        target: str,
        preview_fingerprint: str,
        platform: str = "auto",
        outputs: list[str] | None = None,
        rag_chunks: bool = False,
        output_directory: str = "",
    ) -> dict[str, Any]:
        """用 distill_preview 返回的 preview_fingerprint 创建并启动蒸馏任务；outputs 可选 episodes / skill。"""

        return tools.create_job(
            service,
            target,
            preview_fingerprint,
            platform=platform,
            outputs=outputs,
            rag_chunks=rag_chunks,
            output_directory=output_directory,
        )

    @_register(server, "distill_list_jobs")
    def distill_list_jobs() -> list[dict[str, Any]]:
        """列出本机所有蒸馏任务（按更新时间倒序）：状态、进度计数、创作者与平台。"""

        return tools.list_jobs(service)

    @_register(server, "distill_get_job")
    def distill_get_job(job_id: str) -> dict[str, Any]:
        """读取单个任务的总览：状态、revision、已完成/失败/不支持数量。"""

        return tools.get_job(service, job_id)

    @_register(server, "distill_list_job_items")
    def distill_list_job_items(job_id: str) -> list[dict[str, Any]]:
        """列出任务内每个作品的处理状态、阶段进度与失败原因。"""

        return tools.list_job_items(service, job_id)

    @_register(server, "distill_list_artifacts")
    def distill_list_artifacts(job_id: str) -> list[dict[str, Any]]:
        """列出任务已产出的文件（transcript / cleaned / knowledge），只给文件名、大小与哈希。"""

        return tools.list_artifacts(service, job_id)

    @_register(server, "distill_read_artifact")
    def distill_read_artifact(
        job_id: str,
        source_id: str,
        kind: str,
        max_chars: int = tools.MAX_ARTIFACT_CHARS,
    ) -> dict[str, Any]:
        """读取一个产物的文本内容（Markdown/JSON）；超长内容会被显式截断并标记 truncated。"""

        return tools.read_artifact(
            service,
            job_id,
            source_id,
            kind,
            max_chars=max_chars,
        )

    @_register(server, "distill_control_job")
    def distill_control_job(
        job_id: str,
        action: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """控制任务：pause / resume / retry_failed。默认使用任务当前 revision。"""

        return tools.control_job(
            service,
            job_id,
            action,
            expected_revision=expected_revision,
        )

    return server


def run(
    service: Any,
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8877,
) -> None:
    """Run the MCP server until the client disconnects."""

    original_stdout = sys.stdout
    if transport == "stdio":
        sys.stdout = _StdoutShim(original_stdout, sys.stderr)
    try:
        server = build_server(service, host=host, port=port)
        server.run(transport=transport)
    finally:
        sys.stdout = original_stdout
