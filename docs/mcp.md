# MCP Server：把蒸馏能力交给其他 AI Agent

Distill-Everything 可以作为一个 **MCP（Model Context Protocol）Server** 运行，让 Claude Desktop、Cursor、IDE 插件或自研 Agent 直接调用「预检 → 建任务 → 看进度 → 读产物」整条链路，而不需要它们的用户去学 CLI。

## 安装

```bash
pip install -r requirements.txt -r requirements-mcp.txt
```

`requirements-mcp.txt` 是可选依赖，只有跑 `main.py mcp` 时才需要。

## 启动

```bash
# stdio：给桌面客户端（Claude Desktop / Cursor）用
python main.py mcp

# 本地 HTTP：给支持 SSE / Streamable HTTP 的客户端用
python main.py mcp --transport sse --host 127.0.0.1 --port 8877
python main.py mcp --transport streamable-http --port 8877
```

stdio 模式下，应用自身的日志被改道到 `stderr`，`stdout` 只承载协议帧——不会因为进度输出把 JSON-RPC 弄坏。

## 客户端配置

Claude Desktop（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "distill-everything": {
      "command": "C:\\Python314\\python.exe",
      "args": ["main.py", "mcp"],
      "cwd": "D:\\Vibe\\Distill-Everything"
    }
  }
}
```

Cursor（`.cursor/mcp.json`）结构相同。若用 HTTP：

```json
{
  "mcpServers": {
    "distill-everything": {
      "url": "http://127.0.0.1:8877/sse"
    }
  }
}
```

## 工具列表

| 工具 | 作用 | 是否写状态 |
| --- | --- | --- |
| `distill_list_platforms` | 平台、作品类型、登录状态 | 否 |
| `distill_preview` | 解析并枚举目标，返回 `fingerprint` | 否 |
| `distill_create_job` | 用 `fingerprint` 创建并启动任务 | 是 |
| `distill_list_jobs` / `distill_get_job` | 任务总览与进度计数 | 否 |
| `distill_list_job_items` | 每个作品的状态、阶段进度、失败原因 | 否 |
| `distill_list_artifacts` | 产物清单（文件名、大小、sha256） | 否 |
| `distill_read_artifact` | 读取产物文本（超长显式截断） | 否 |
| `distill_control_job` | `pause` / `resume` / `retry_failed` | 是 |

典型调用顺序：

```text
distill_list_platforms
  → distill_preview(target, platform="auto")
  → distill_create_job(target, preview_fingerprint, outputs=["skill"])
  → distill_get_job / distill_list_job_items（轮询）
  → distill_list_artifacts → distill_read_artifact
```

`distill_preview` 的 `fingerprint` 是防呆：没有预检结果就不允许建任务，避免 Agent 对着一个拼错的链接跑完整条流水线。

## 安全边界

- **不传凭据**：MCP 工具既不接收 Cookie/二维码，也不返回任何凭据；登录必须在本地 Dashboard 页内弹窗或 CLI 完成。
- **不外传绝对路径**：产物只暴露文件名、大小、sha256 与文本内容，目录信息不出 MCP 边界。
- **会自动脱敏**：所有返回值都过一遍 `redact_value`，即便标题里混进 `SESSDATA=...` 也会被打码。
- **本地进程**：stdio 由客户端拉起；HTTP 传输默认只绑 `127.0.0.1`，不是公网服务。

## 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 客户端启动即断开 | `mcp` 未安装 | `pip install -r requirements-mcp.txt` |
| `distill_preview` 报认证错误 | 平台要求登录 | 在 Dashboard「平台与登录」扫码，或 `python main.py source login <platform>` |
| `distill_create_job` 报 fingerprint 不匹配 | 目标内容变了 | 重新调用 `distill_preview` 取新 `fingerprint` |
| HTTP 模式连不上 | 端口被占 / 绑定地址不对 | `--port` 换端口；确认用 `--host 127.0.0.1` |
| stdio 偶发解析错误 | 有第三方库直接往 stdout 打印 | 用 stdio 时避免在工具调用链里加 `print`；应用内日志已改道 stderr |
