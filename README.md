# Distill-Everything

[![pytest](https://github.com/AITCX08/Distill-Everything/actions/workflows/pytest.yml/badge.svg)](https://github.com/AITCX08/Distill-Everything/actions/workflows/pytest.yml)
[![Dashboard](https://github.com/AITCX08/Distill-Everything/actions/workflows/dashboard.yml/badge.svg)](https://github.com/AITCX08/Distill-Everything/actions/workflows/dashboard.yml)
[![CodeQL](https://github.com/AITCX08/Distill-Everything/actions/workflows/codeql.yml/badge.svg)](https://github.com/AITCX08/Distill-Everything/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB.svg)](./requirements.txt)

> Worker 编排运行时：每个作品由一个隔离的子进程执行完整流水线。浏览器只控制本地任务；不会收到 Cookie、命令行、PID、二维码载荷或原始 Worker 输出。保存目录仅能在当前本地会话保护的创建确认与任务交付详情中查看。发布架构见 `docs/superpowers/specs/2026-08-09-worker-orchestrated-distillation-design.md`。

## Dashboard Worker 工作流

1. 打开仅监听本机回环地址的 Dashboard，在本地登录弹窗中完成扫码。
2. 创建创作者任务或导入本地 Bilibili 系列。导入系列会成为一个任务，其中每个分集都有独立、可恢复的执行单元；已完成分集保持完成状态。
3. `TaskManager` 最多启动两个无窗口 Worker。每个 Worker 独立完成下载、音频提取、转写、清洗、摘要与 Markdown 交付。
4. 作品卡仅在下载阶段显示字节数、速度和 ETA；ASR 与 LLM 阶段显示真实阶段与检查点，不伪造下载数据。
5. 暂停、继续或取消一个作品不会影响其他作品。Worker 会在下一个持久化边界停止；重启协调器会识别仍存活的租约，或将缺失租约的任务标为可恢复。

### 资源与隐私策略

- 默认限制为两个流水线 Worker、两个下载、一个 ASR 阶段和一个 LLM 阶段。Worker 只有取得 `TaskManager` 发放的阶段许可后才会消耗受限资源。
- Dashboard 只绑定 `127.0.0.1`。Cookie、二维码载荷、凭据、命令行、PID 和原始 Worker 输出不会进入浏览器、SSE 或日志。
- Worker 进度保存在任务私有 JSONL 中，服务端先校验和脱敏，再投影为紧凑的 SSE 快照。
- **数据出网边界**：转写在本机完成（FunASR）。清洗、摘要与知识提取会把相应文本发送到你配置的 LLM 端点；首次运行 FunASR 会从 ModelScope 下载模型权重。需要完全离线时设置 `LLM_PROVIDER=ollama` 并预先缓存模型。

Dashboard 始终是本地单用户工具：广泛订阅的任务列表、SSE 和实时日志只包含脱敏状态；绝对保存目录只可通过本地会话保护的详情接口提供给当前页面。

> 将公开内容转化为可复用的结构化知识：逐作品 Markdown、聚合 Skill 与 RAG 知识块。

Distill-Everything 是一个本地优先的内容蒸馏工具。它可以从创作者主页枚举可见作品，完成下载、转写、清洗、知识提取与输出；也保留 PDF、DOCX、TXT 文档蒸馏和会议纪要能力。

## 支持矩阵

| 范围 | 状态 | 边界 |
| --- | --- | --- |
| Windows CLI、Dashboard 与本地工作流 | 正式支持 | Dashboard 仅监听 `127.0.0.1`。 |
| macOS 安装、CLI、Dashboard 与基础 CI | 正式文档支持 | 使用前台 Dashboard 命令；详细限制见[平台支持与故障排查](./docs/平台支持与故障排查.md)。 |
| Bilibili / 抖音扫码、Playwright 浏览器 | 需要设备验收 | 登录二维码在 Dashboard 页内弹窗展示；抖音需要 `playwright install chromium`，失败时提示改用 CLI。 |
| 抖音收藏夹 / 合集（`/collection/<id>`、`user/<sec_uid>?showTab=collect`） | 实验性 | 需登录 + Playwright Chromium；接口路径随抖音版本变化，失败会明确报错而不是混入无关视频。 |
| YouTube（`@handle`、`/channel/UC…`、单视频） | 实验性 | 走 `yt-dlp`，公开内容无需登录；不处理会员、付费或需登录的视频。 |
| Apple Silicon FunASR MPS | 需要设备验收 | 不承诺与 CUDA 或 CPU 相同的性能和稳定性。 |

公开 issue、截图和日志中不得包含 Cookie、二维码、浏览器 profile、API Key、媒体原文件、真实任务标题或本地绝对路径。

## 三分钟上手

1. 按下方对应系统的安装步骤创建环境并安装依赖。
2. 复制 `config.example.env` 为 `.env`，填写你选择的 LLM 提供商配置。
3. 运行 `python main.py source platforms` 检查可用平台；需要图形界面时启动本地 Dashboard。

遇到平台、浏览器、端口或 macOS 问题，请先阅读[平台支持与故障排查](./docs/平台支持与故障排查.md)。

当前正式支持 Bilibili 与抖音。平台接入、内容处理和输出形式相互解耦：增加一个平台不需要复制蒸馏流水线，增加一种输出也不需要了解平台的私有 ID 或抓取细节。

## 核心能力

- **多平台创作者蒸馏**：通过统一的 Source Adapter 接入 Bilibili、抖音（含收藏夹/合集）与 YouTube；`--platform auto` 按链接自动选平台，新增平台只需实现并注册适配器。
- **双输出形态**：每件作品生成独立 Markdown（episodes），也可聚合为一个 `SKILL.md`；两者可同时生成。
- **可恢复流水线**：任务状态、产物哈希与阶段进度持久化；中断后从最近的有效阶段恢复，支持单项重试。
- **真实进度与本地 Dashboard**：下载字节进度、阶段进度、ETA、实时 trace、暂停/恢复/取消和产物预览。
- **中文 ASR 与 LLM 知识提取**：FunASR 自动选择 CUDA / Apple Silicon MPS / CPU；支持 Claude、OpenAI、Qwen、DeepSeek 和 Ollama。
- **文档、融合与 RAG**：PDF/DOCX/TXT 按章节蒸馏；视频与书籍可融合为一个 Skill；可输出中立的 RAG chunks JSON。
- **本地安全边界**：Dashboard 固定监听 `127.0.0.1`；登录在外部浏览器完成，界面不展示或保存二维码、Cookie、API Key。

## 架构概览

```text
CLI / Local Dashboard
          │
          ▼
  DistillationService
          │
          ├── PlatformManager ── PlatformRegistry
          │       ├── BilibiliAdapter
          │       └── DouyinAdapter
          │
          ├── SourceDistillationRunner
          │       ├── 下载队列（默认 3）
          │       ├── ASR 队列（默认 1）
          │       └── LLM 队列（默认 3）
          │
          ├── JobState / ArtifactStore / EventHub
          │
          └── OutputManager ── OutputRegistry
                  ├── episodes/*.md
                  ├── SKILL.md
                  └── RAG chunks JSON
```

领域边界如下：

| 层 | 职责 |
| --- | --- |
| `src/platforms/` | 识别链接、登录、枚举作品、刷新素材地址和下载；不调用 ASR 或 LLM。 |
| `src/application/` | `DistillationService`、作业仓库、事件、并发租约、状态查询与控制。 |
| 既有处理模块 | `crawl`、`asr`、`clean`、`model`、`generate`、`rag` 负责可复用领域能力。 |
| `src/outputs/` | 将规范化产物写为 episodes、Skill 或 RAG；不依赖平台私有字段。 |
| `src/dashboard/` + `dashboard/` | FastAPI/SSE 本地服务与 React 作战台，均通过应用服务访问同一任务状态。 |

## 快速开始

### 1. 安装

要求：Python 3.10+（CI 使用 3.11）、`ffmpeg`。抖音适配器还需要 Playwright Chromium。

```bash
git clone https://github.com/AITCX08/Distill-Everything.git
cd Distill-Everything
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
winget install ffmpeg
```

macOS zsh：

```zsh
brew install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

安装 `ffmpeg`：Windows 可使用 `winget install ffmpeg`；macOS 使用 `brew install ffmpeg`；Ubuntu/Debian 使用 `sudo apt install ffmpeg`。

### 2. 配置

复制模板并填写一个可用的 LLM 提供商配置：

Windows PowerShell：

```powershell
Copy-Item config.example.env .env
```

macOS zsh：

```zsh
cp config.example.env .env
```

`.env` 中常用项目：

| 配置 | 用途 |
| --- | --- |
| `LLM_PROVIDER` | `claude`、`openai`、`qwen`、`deepseek` 或 `ollama`。 |
| 对应的 `*_API_KEY` | 使用云端 LLM 时填写；Ollama 不需要 API Key。 |
| `DATA_DIR` / `OUTPUT_DIR` | 中间产物和最终产物的根目录。 |
| `DOUYIN_PROFILE_DIR` | 抖音外部 Chromium 的持久化登录目录，默认在 `data/browser/douyin`。 |
| `DISTILL_*` | 多平台任务的并发、重试、临时媒体保留策略。 |

不要提交 `.env`、浏览器 profile、Cookie、下载媒体、任务状态或实际蒸馏产物。

### 3. 蒸馏一个创作者

先查看平台能力与登录状态。对需要认证的平台，使用外部可见浏览器扫码：

```bash
python main.py source platforms
python main.py source status douyin
python main.py source login douyin
```

创建者任务默认同时产出 episodes 与 Skill。建议先预检，确认枚举结果后再执行正式任务：

```bash
# 仅解析和枚举：不下载、不调用 ASR/LLM、不创建任务产物
python main.py source creator "https://www.douyin.com/user/SEC_UID" --dry-run

# 抖音：默认输出 episodes + skill
python main.py source creator "https://www.douyin.com/user/SEC_UID"

# Bilibili 走同一入口
python main.py source creator "https://space.bilibili.com/12345678" --platform bilibili

# 直接蒸馏 Bilibili 单个视频（支持 b23.tv、BV 和 av）
python main.py source creator "https://b23.tv/短链" --platform bilibili
python main.py source creator "BV1xxxxxxxxxxxx" --platform bilibili

# YouTube：频道或单个视频（走 yt-dlp，公开内容无需登录）
python main.py source creator "https://www.youtube.com/@handle" --platform youtube
python main.py source creator "https://youtu.be/VIDEO_ID"

# 不给 --platform 时按链接自动识别平台（默认 auto）
python main.py source creator "https://www.youtube.com/@handle"
```

常用选项：

```bash
# 只保留逐作品 Markdown；或者只生成 Skill
python main.py source creator "<创作者主页>" --emit episodes
python main.py source creator "<创作者主页>" --emit skill

# 追加 RAG chunks，保留下载媒体，或只重试失败作品
python main.py source creator "<创作者主页>" --rag-chunks --keep-media
python main.py source creator "<创作者主页>" --resume --retry-failed

# 根据机器资源调整并发；ASR 始终限定为一个 worker
python main.py source creator "<创作者主页>" \
  --download-workers 3 --asr-workers 1 --llm-workers 3 --max-active 3
```

任务状态保存在 `data/jobs/<platform>/<creator-id>/job_state.json`。每个视频以下载 → 转写 → 清洗 → 知识提取 → 输出的顺序处理；图文作品当前会被明确标记为 `unsupported_note`，纳入 coverage，但不会被错误地报告为完成。

## 本地 Dashboard

Dashboard 是单用户、本地作战台，使用 FastAPI + SSE + React。它和 CLI 复用同一个 `DistillationService`、任务状态和事件流。

![空工作台实拍：任务概览、作品执行队列与创建入口](./docs/images/dashboard-workbench.png)

*本机真实界面实拍（1600×1000，空工作台状态）；不含真实任务、平台标识、保存位置或登录信息。*

| 平台 | 启动方式 |
| --- | --- |
| Windows PowerShell | 使用隐藏启动：先将 `DISTILL_EVERYTHING_PYTHON` 设置为项目 `python.exe`，再用 `pythonw.exe` 与 `Start-Process -WindowStyle Hidden` 启动。 |
| macOS zsh | 前台运行 `python main.py dashboard --port 8765 --no-open`，再访问 `http://127.0.0.1:8765/`。 |

Windows PowerShell（不弹出 CMD 窗口）：

```powershell
$python = $env:DISTILL_EVERYTHING_PYTHON
if (-not $python) { throw '请先设置 DISTILL_EVERYTHING_PYTHON 为项目 python.exe 路径。' }
$pythonw = Join-Path (Split-Path $python -Parent) 'pythonw.exe'
Start-Process -FilePath $pythonw -ArgumentList 'main.py dashboard --port 8765 --no-open' -WorkingDirectory $PWD -WindowStyle Hidden
```

macOS zsh：

```zsh
python main.py dashboard --port 8765 --no-open
```

通用 CLI 调试方式：

```bash
# 默认启动后打开 http://127.0.0.1:8765
python main.py dashboard

# 不自动打开浏览器，或指定端口
python main.py dashboard --no-open
python main.py dashboard --port 9000
```

Dashboard 提供：

- 无活动任务时仍保留完整工作台：0% 总览、空任务队列、创建任务快捷入口和本地隐私说明。
- 平台状态与本地扫码登录：所有平台都在页面内弹窗展示二维码，页面不会保存 Cookie 或二维码载荷。
- 来源预检、输出模板预览、默认保存位置和单次覆盖保存位置。
- 可读的作品标题、实时下载/阶段进度、ETA 与脱敏 trace。
- 暂停、恢复、取消和失败单项重试。
- 作业历史、产物列表、只读文本预览、复制以及打开该任务已批准的本地保存位置。

服务只绑定 `127.0.0.1`，并对会话、Origin 与写操作使用本地会话/CSRF 校验。它不是局域网或公网服务；请勿通过端口转发把它暴露到不受信任网络。

### 创作者工作台

1. 在“新建任务”中粘贴创作者主页并执行预检，确认创作者、作品数量和登录状态。
2. 选择“逐作品 Markdown”“蒸馏 Skill”或“RAG 分块”；每项均可打开示例，先了解生成结果。
3. 使用已设置的默认保存位置，或勾选“本次使用其他保存位置”并完成本地校验。
4. 在“任务作战台”中查看标题优先的执行信息。下载阶段才显示传输速度；转写、摘要和写入阶段显示真实状态与检查点。
5. 完成后点击“查看产物”，在“产物库”预览文本或打开已批准的任务保存位置。保存目录仅在这一受保护的本地详情中显示。

## 输出与目录

创作者任务会按创作者隔离输出：

```text
output/
└── <creator>-<platform>-<creator-id>/
    ├── episodes/
    │   └── <item-id>.md
    ├── rag/
    │   └── <platform>_<item-id>.json
    └── SKILL.md

data/
├── jobs/<platform>/<creator-id>/job_state.json
├── browser/douyin/                 # 本地登录 profile
├── transcripts/                    # 转写 JSON
├── cleaned/                        # 清洗后的结构化文本
├── knowledge/                      # 单项知识与聚合画像
└── rag_chunks/                     # 中立 RAG chunks JSON
```

`episodes/*.md` 适合逐篇阅读、归档与检索；`SKILL.md` 是以全部有效作品为语料生成的聚合人格/知识文件。两种输出可独立选择。RAG 是第三种可组合输出，不影响 episodes/Skill 的选择。

## 保留的文档与会议能力

多平台架构没有替代原有能力，以下命令继续可用：

```bash
# PDF / DOCX / TXT：默认章节化蒸馏并生成 RAG chunks
python main.py distill --file "书籍.pdf" --name "作者名" --llm deepseek

# 视频与书籍章节作为对等素材融合为一个 Skill
python main.py fuse --name "人物名" --llm deepseek \
  --sources "BV*" --sources "BOOK_书名_*"

# 为已有素材重建 RAG chunks
python main.py chunks --source-id "BOOK_书名_*"

# 飞书妙记/会议纪要入口
python main.py meeting --help
python main.py feishu-meeting --help
```

旧版 Bilibili 分阶段命令 `login`、`crawl`、`asr`、`clean`、`model`、`generate`、`run` 也保留，便于已有脚本和工作流迁移；新项目优先使用 `source creator` 或 Dashboard。

## 给 AI Agent 用（MCP Server）

本项目可以直接作为 **MCP Server** 运行，把「预检 → 建任务 → 看进度 → 读产物」暴露成 9 个工具，供 Claude Desktop、Cursor 或自研 Agent 调用：

```bash
pip install -r requirements-mcp.txt
python main.py mcp                        # stdio，给桌面客户端
python main.py mcp --transport sse --port 8877   # 本地 HTTP
```

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

工具清单、调用顺序、安全边界与排障见 [docs/mcp.md](./docs/mcp.md)。要点：MCP 不接收也不返回任何凭据，登录仍在 Dashboard 页内弹窗或 CLI 完成；产物只给文件名与内容，不外传绝对路径。

## 扩展一个平台或输出

新增平台时，在 `src/platforms/` 实现 `PlatformAdapter` 并注册到 `PlatformRegistry`。适配器只负责平台边界：匹配、认证、解析、枚举、刷新和下载。

新增输出时，在 `src/outputs/` 实现 `OutputTarget` 并注册到 `OutputRegistry`。输出目标声明需要哪些规范化产物，然后消费单项产物或在 corpus 完成后统一 `finalize()`。

因此，未来接入小红书或 OCR 图文处理时，不需要修改 episodes/Skill/RAG 输出层；新增 Notion、Obsidian 等输出时，也不需要认识 `bvid`、`aweme_id` 等平台细节。详细的数据契约、恢复策略和扩展指南见 [DEVELOPMENT.md](./DEVELOPMENT.md)。

## 前端开发与验证

发布包已经包含构建后的 Dashboard 静态资源，普通使用者不需要 Node.js。修改前端时需要 Node.js **24.x**：

```bash
cd dashboard
npm ci
npm run build
npm test
npm run e2e
```

Python 测试在项目根目录直接运行 `python -m pytest` 即可。Windows 桌面端若遇到长命令流不稳定，可用项目自带的后台运行器：

```powershell
$env:DISTILL_EVERYTHING_PYTHON = '<path-to-project-python.exe>'
cmd /d /c start "" /b scripts\run-pytest-background.cmd tests\dashboard\test_output_directory.py
```

随后读取 `.local-artifacts/test-runs/latest.exitcode` 与日志确认结果。

提交前至少执行与改动范围相符的测试，并检查 Markdown 与前端构建产物是否同步。完整开发规范、数据契约和架构细节请阅读：

- [DEVELOPMENT.md](./DEVELOPMENT.md)：长期维护文档、模块职责、数据格式与扩展点。
- [CLAUDE.md](./CLAUDE.md)：AI 编程协作约定与仓库反模式。
- [迁移到 Distill-Everything](./docs/迁移到-Distill-Everything.md)：旧名称、目录、GitHub 地址与本机启动方式的映射。
- [多平台设计](./docs/superpowers/specs/2026-07-21-multi-platform-distillation-design.md)：Source Adapter 与双输出设计。
- [Dashboard 设计](./docs/superpowers/specs/2026-07-21-local-dashboard-design.md)：本地 Dashboard 的接口、安全与交互设计。
- [创作者工作台设计](./docs/superpowers/specs/2026-08-10-dashboard-creator-workbench-design.md)：创建、执行与交付闭环。

## 使用边界

仅处理你有权访问、保存和再利用的公开内容，并遵守平台条款、版权规则和当地法律。登录信息仅应保存在你控制的本地设备上；不要在 issue、日志、截图或提交中泄露 Cookie、二维码、API Key 或个人数据。

## 许可证

[MIT License](./LICENSE)
