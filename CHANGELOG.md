# 更新日志

本项目采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，并遵循语义化版本号。

## [Unreleased]

### 新增

- **创作者中心采集（Insights）**：`python main.py insights --platform bilibili|douyin|xiaohongshu`
  用已登录的持久 profile 打开后台并拦截 XHR，归一化为 `InsightsSnapshot`（缺失指标保持 `null`，
  不会误记成 0）；默认关闭，需 `INSIGHTS_ENABLED=1`。
- **小红书平台支持**：新增 `src/platforms/xhs/`，支持笔记链接（含 `xhslink.com` 短链）、
  用户主页、收藏（`xhs:collect:<user_id>`）与图文/视频下载；走 Spider_XHS 风格的
  sidecar 契约（`/xhs/note`、`/xhs/user/notes`、`/xhs/user/collect`），路径可覆盖。
- **B站收藏夹**：`space.bilibili.com/{mid}/favlist?fid=` / `bilibili:fav:{fid}`，
  三个公开 GET，按 `attr==1` 过滤失效条目。
- **抖音 sidecar**：`DOUYIN_SOURCE=auto|sidecar|browser`，端点取自 Evil0ctal V4.1.2。
- **开发文档**：`docs/platform-sources.md`（四层架构、三平台接口清单、配置、里程碑、验证记录）。

- **MCP Server**：`python main.py mcp`（stdio / sse / streamable-http），暴露 9 个工具
  （`distill_list_platforms`、`distill_preview`、`distill_create_job`、`distill_list_jobs`、
  `distill_get_job`、`distill_list_job_items`、`distill_list_artifacts`、`distill_read_artifact`、
  `distill_control_job`），供 Claude Desktop / Cursor / 自研 Agent 调用；可选依赖 `requirements-mcp.txt`，
  stdio 模式下应用日志改道 stderr，避免污染 JSON-RPC 帧。
- 抖音收藏夹 / 合集自动蒸馏：新增 `collection` target 类型，支持
  `https://www.douyin.com/user/self?showTab=collect`、`https://www.douyin.com/collection/<id>`
  与显式写法 `douyin:collection:<id>`；解析不启动浏览器，枚举只接受收藏类 JSON 端点。
- 抖音 **Dashboard 页内扫码登录**：`DouyinLoginCoordinator` 用无头持久上下文截取登录二维码，
  经本地接口提供给弹窗；拿不到二维码时回退提示 CLI 的外部浏览器流程。

### 变更

- 抖音浏览器会话支持复用系统 Chrome/Edge：`launch_persistent_context` 按 `bundled → chrome → msedge`
  顺序回退，未执行 `playwright install chromium` 的机器也能用；三者都不可用时给出可执行提示。

- 登录接口统一为 `/api/v1/platforms/<platform>/login[/<op>[/qr]]`，Bilibili 与抖音共用同一套前端弹窗。
- 抖音认证提示改为中文页内引导，不再提示“在外部浏览器登录”。

### 修复

- CLI 不再把 `auth_status="not_required"` 当成认证失败：此前 YouTube 会在预检阶段直接报
  “YouTube 公开内容无需登录”并退出。
- YouTube 适配器在 `yt-dlp` 可执行文件不在 PATH 时回退到 `python -m yt_dlp`。

## [0.4.0] - 2026-08-13

首个公开 Beta。核心变化是把「单机脚本流水线」升级为「多平台 + 受控 Worker + 本地 Dashboard」。

### 新增

- Source Adapter 平台层与 episodes、Skill、RAG 分块三类输出层。
- 受控 Worker：每个作品在独立子进程中完成下载、音频、转写、摘要和交付，可暂停、恢复与重试。
- 本地 Dashboard：展示脱敏状态、真实阶段、任务队列、产物预览和平台登录入口。
- Linux、Windows、macOS 基础 Python CI 与 Node 24 Dashboard CI，另加 Ruff 与 CodeQL 静态检查。
- Windows 与 macOS 的安装、启动、排障及安全边界文档。

### 变更

- 抖音浏览器会话支持复用系统 Chrome/Edge：`launch_persistent_context` 按 `bundled → chrome → msedge`
  顺序回退，未执行 `playwright install chromium` 的机器也能用；三者都不可用时给出可执行提示。

- 产品与仓库名称统一为 Distill-Everything。
- Dashboard 固定仅监听本机回环地址。
- `src.outputs` / `src.platforms` / `src.distillation` 改为 PEP 562 惰性导出，导入任一层不再触发整包加载。

### 修复

- 修复循环导入：`import src.outputs` 或单独运行 `pytest tests/outputs` 会抛 `ImportError: cannot import name 'ArtifactKind' from partially initialized module`。
- 修复任务初始化崩溃：`SourceCreator.raw_metadata`（`MappingProxyType`）参与 `dataclasses.asdict` 导致 `TypeError: cannot pickle 'mappingproxy' object`；现在 `job_state.json` 只持久化公开身份字段。
- 修复非正常关闭后 Dashboard 无法启动：`psutil.NoSuchProcess` 不被 `OSError` 捕获，遗留的 TaskManager owner 行会让启动直接抛异常。
- 修复 Windows 上 `os.kill(pid, 0)` 误发 CTRL+C：该调用被翻译成 `GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)`，会打断整个控制台进程组（这也是此前「Windows 测试不稳定」的真实原因）。新增 `src/application/process.py::process_is_alive`。
- 修复 Windows 文件共享冲突：worker 删除 `resource-request.json` 时管理器正在读它，`WinError 32` 会让 worker 以退出码 1 结束。新增 `src/fsutil.py` 的 `replace_with_retry` / `unlink_with_retry`。
- 修复 `src/model/knowledge_extractor.py` 中散落在函数之间的模块级 import（潜在的导入顺序脆弱点）。
- 清理仓库内 Ruff 报出的未使用导入与未使用变量。

### 已知限制

- Bilibili / 抖音扫码、Playwright 浏览器行为和 Apple Silicon FunASR MPS 需要在真实 macOS 设备上验收。
- 不应将 Dashboard 暴露到局域网或公网。
- 使用云端 LLM 时，清洗与摘要文本会发送到对应服务商；需要完全离线请使用 Ollama。
