# Distill-Everything 开发文档

> 本文档面向两类读者:**二次开发者** 与 **AI 辅助编程 Agent**。阅读完本文档后,你应当能够在不阅读全部源码的前提下,对任一阶段进行定位、修改、扩展。
>
> **文档维护原则**:代码结构或数据契约发生变化时,必须同步更新本文档对应章节。每次大改后在文末 [变更记录](#变更记录) 追加一行。

---

## 目录

1. [整体架构](#1-整体架构)
2. [数据流与契约](#2-数据流与契约)
3. [模块详解](#3-模块详解)
4. [配置系统](#4-配置系统)
5. [LLM 抽象层](#5-llm-抽象层)
6. [断点续传与完整性校验](#6-断点续传与完整性校验)
7. [错误处理与重试策略](#7-错误处理与重试策略)
8. [扩展点](#8-扩展点)
9. [常见修改场景手册](#9-常见修改场景手册)
10. [开发规范](#10-开发规范)
11. [调试与排查](#11-调试与排查)
12. [已知限制与 TODO](#12-已知限制与-todo)
13. [变更记录](#13-变更记录)

---

## 1. 整体架构

### 1.1 设计理念

Distill-Everything 采用 **5 阶段流水线 + 文件系统作为中间态** 的架构:

- 每个阶段是**幂等的**:重复运行只处理新/损坏的文件。
- 阶段间通过 JSON 文件**解耦**,无进程内共享状态。
- 任一阶段可独立运行:用户可以手动编辑中间文件后继续下一步。
- 每个阶段产物都是 **RAG 兼容** 的结构化 JSON,可被外部系统直接消费。

### 1.2 阶段图

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  ① crawl │───→│   ② asr  │───→│ ③ clean  │───→│ ④ model  │───→│⑤ generate│
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
     ↓                ↓                ↓                ↓                ↓
data/audio/    data/transcripts/  data/cleaned/  data/knowledge/  output/*.skill.md
data/video_list.json                                    ↓
                                              blogger_profile.json
```

### 1.3 技术栈映射

| 层级 | 组件 | 作用 |
|---|---|---|
| CLI | `click` + `rich` | 子命令、进度条、彩色输出 |
| 爬取 | `bilibili-api-python` + `yt-dlp` | 视频列表 / 音频下载 |
| ASR | `funasr` + `torch` + `modelscope` | 语音转文字(paraformer-zh) |
| 文本 | 正则 + `anthropic` / `openai` SDK | 口语清洗 + 主题切分 |
| 建模 | LLM(同上) + `dataclasses` | 知识提取 + 画像合成 |
| 模板 | `jinja2` | SKILL.md 渲染 |
| 配置 | `python-dotenv` + `pydantic` | .env → 校验 → 注入 |

### 1.4 目录结构

```
Distill-Everything/
├── main.py                            # 入口:click 子命令,每条命令是一个阶段
├── requirements.txt                   # Python 依赖(不含 torch — funasr 未硬声明)
├── config.example.env                 # 环境变量模板
├── README.md                          # 用户文档
├── DEVELOPMENT.md                     # 本文档
├── src/
│   ├── __init__.py
│   ├── config.py                      # Pydantic 模型 + load_config()
│   ├── crawl/
│   │   ├── auth.py                    # 认证:二维码扫码登录 + 凭证缓存(三级策略)
│   │   ├── video_list.py              # 阶段1-a:UP 主投稿列表(async)
│   │   ├── audio_download.py          # 阶段1-b:yt-dlp 下载 + 完整性检查
│   │   └── subtitle.py                # 可选:官方 CC 字幕(目前未被主流程调用)
│   ├── asr/
│   │   └── funasr_engine.py           # 阶段2:FunASR 封装 + 设备检测 + OOM 重试
│   ├── clean/
│   │   └── text_processor.py          # 阶段3:规则清洗 + LLM 主题切分 + LLM 客户端工厂
│   ├── model/
│   │   └── knowledge_extractor.py     # 阶段4:nuwa-skill 知识提取 + 画像合成 + JSON 容错
│   └── generate/
│       └── skill_generator.py         # 阶段5:Jinja2 模板渲染
├── templates/skill.md.j2              # SKILL.md 模板(nuwa-skill 风格)
├── prompts/                           # LLM Prompt 的只读参考(实际 Prompt 在代码中)
├── examples/sample.skill.md           # 示例输出
├── data/                              # 运行时产物,.gitignore
└── output/                            # 最终产物,.gitignore
```

### 1.5 v0.4 跨平台架构

新入口遵循固定分层，CLI 与本地 Dashboard 都不得绕过应用服务直接操作平台或状态文件：

```text
CLI / Dashboard
  -> src/application/        命令、查询、revision、events、job lease
  -> src/platforms/          Bilibili/Douyin Source Adapter 与显式 registry
  -> src/distillation/       原子状态、artifact、3/1/3 engine、progress、双 ETA
  -> src/outputs/            episodes / skill / rag 可组合输出目标
```

跨平台内部主键是 `<platform>_<item_id>`。`bvid` 只作为旧 B 站字段保留兼容；新代码必须优先使用 `source_id`。抖音枚举只信任页面产生的 `/aweme/post/` JSON，按 cursor 原子保存 checkpoint。运行态为 `pending -> enumerated -> downloading -> transcribing -> cleaning -> summarizing -> writing -> completed`，`failed/retry_wait/unsupported` 为旁路状态。

所有状态和正式产物使用同目录临时文件、`flush + fsync`、重新打开校验、`os.replace`。读取损坏的 `job_state.json` 必须抛 `StateCorruptionError`。同一任务执行前必须获取 job lease，同一抖音 profile 也必须排他使用。

默认并发是下载 3、ASR 1、LLM 3，且全局活跃作品不超过 3。FunASR 只能由单一 ASR worker 持有一份模型实例。Rich 与 Dashboard 只消费同一个不可变 `ProgressSnapshot`，前端不得自行重算百分比或 ETA。

> ⚠️ `prompts/*.txt` 目前只是参考文档。**实际 Prompt 模板内嵌在 Python 源文件底部**(`TOPIC_SEGMENT_PROMPT`、`VIDEO_KNOWLEDGE_PROMPT`、`PROFILE_SYNTHESIS_PROMPT`)。修改 Prompt 时改 Python 文件,不要改 `prompts/` 目录。

### 1.6 编排、Worker 与本地 Dashboard

v0.4 在应用服务之上增加了一层**进程编排**，Dashboard 只是它的一个客户端：

```text
Dashboard (FastAPI + SSE, 127.0.0.1)
  -> TaskManager（唯一持有 Worker 进程的主人）
       -> worker 子进程（无窗口，每作品一个）
            -> BilibiliWorkPipeline / DistillationEngine
  -> OrchestrationStore（SQLite：任务、租约、资源闸门、事件）
```

硬约束：

- **进程所有权**：同一 `DATA_DIR` 只能有一个存活的 `TaskManager`。启动时 `claim_ownership()` 读取持久化 owner 行；owner 进程已死则抢占，仍存活则拒绝启动。PID 探活必须把 `psutil.NoSuchProcess` 视为“已退出”（它继承 `psutil.Error`，**不是** `OSError`），否则非正常关闭后 Dashboard 无法再次启动。
- **资源闸门**：Worker 只有写入 `resource-request.json` 并读到 `resource-grant.json` 后才能进入受限阶段；ASR 全局只允许一个持有者。
- **状态可见性**：Worker 把进度写进任务私有 JSONL；服务端校验 + 脱敏后才投影为 SSE 快照。绝对路径只在本地会话保护的详情接口返回。
- **凭据边界**：`job_state.json` 的 `creator` 只保存 `platform / creator_id / display_name / canonical_url`。`SourceCreator.raw_metadata` 是 `MappingProxyType`（不可 `asdict`/deepcopy），属于适配器内存态，**不得持久化**——需要新字段时先加白名单，不要直接 `asdict(creator)`。
- **导入约定**：`src.outputs` / `src.platforms` / `src.distillation` 的 `__init__.py` 使用 PEP 562 惰性导出。改回即时重导出会重新形成 `outputs → platforms → distillation → outputs` 循环导入（`tests/test_import_order.py` 会失败）。

Dashboard 的会话安全：固定绑定 `127.0.0.1`（`validate_host` 硬性拒绝其它地址）、随机 `distill_session` cookie、写操作要求 `X-Distill-CSRF` 与 `Origin` 同源。

---

## 2. 数据流与契约

所有中间产物的 **schema 即契约**。修改字段需同步更新下游消费者。

### 2.1 `data/video_list.json`

产出者:`src/crawl/video_list.py::save_video_list`
消费者:`main.py::crawl`(合并)、`main.py::asr`(元信息)

```json
[
  {
    "bvid": "BVxxxxx",
    "title": "视频标题",
    "duration": "MM:SS 或 HH:MM:SS",  // B站原始字段,字符串
    "pubdate": 1700000000,             // unix 秒
    "description": "简介",
    "view_count": 10000,
    "comment_count": 100,
    "aid": 123456789
  }
]
```

### 2.2 `data/audio/{bvid}.wav`

产出者:`src/crawl/audio_download.py::download_audio`(yt-dlp)
消费者:`src/asr/funasr_engine.py`

- 格式:WAV(yt-dlp `-x --audio-format wav`)
- 文件名规范:必须是 `BV...` 前缀(asr 阶段用 `glob("BV*.*")` 扫描)
- 完整性判断:`check_audio_completeness()` 比较 wave 实际时长与 `video_list.json` 的 duration,误差 > 30s 视为不完整

### 2.3 `data/transcripts/{bvid}.json`

产出者:`src/asr/funasr_engine.py::save_transcript`
消费者:`src/clean/text_processor.py::process_transcript`

```json
{
  "bvid": "BVxxxxx",
  "title": "视频标题",
  "source": "funasr",
  "model": "paraformer-zh",
  "full_text": "拼接后的完整文本",
  "segments": [
    {
      "id": "BVxxx_seg_0000",
      "text": "这句话",
      "start": 0.0,    // 秒
      "end": 3.2,
      "confidence": 0.0
    }
  ],
  "metadata": { "pubdate": ..., "duration": ..., ... }
}
```

### 2.4 `data/cleaned/{bvid}.json`

产出者:`src/clean/text_processor.py::save_cleaned`
消费者:`src/model/knowledge_extractor.py::extract_from_video`

```json
{
  "bvid": "BVxxxxx",
  "title": "视频标题",
  "source": "funasr",
  "full_text": "去填充词后的完整文本",
  "topics": [
    {
      "id": "BVxxx_topic_000",
      "title": "主题名",
      "content": "主题内容",
      "tags": ["tag1", "tag2"]
    }
  ],
  "segments": [ /* 合并短段后的 segments */ ],
  "metadata": { ... }
}
```

文档章节模式下仍沿用同一 schema，不新增顶层字段，只在 `metadata` 中追加章节信息，以保证
`src/model/knowledge_extractor.py::extract_from_video` 可直接消费：

```json
{
  "bvid": "BOOK_戎震避坑_30450a_ch01",
  "title": "戎震避坑 - 第一章 为什么要避坑",
  "source": "document:戎震避坑.pdf",
  "full_text": "章节全文",
  "topics": [ ... ],
  "segments": [ ... ],
  "metadata": {
    "source_type": "book_chapter",
    "chapter_index": 1,
    "chapter_title": "第一章 为什么要避坑",
    "parent_book_id": "BOOK_戎震避坑_30450a",
    "total_chapters": 12,
    "char_range": [0, 6821],
    "file_path": "/abs/path/戎震避坑.pdf",
    "file_format": ".pdf",
    "char_count": 6821,
    "segment_count": 4
  }
}
```

### 2.5 `data/knowledge/{bvid}.json` — 单视频知识

产出者:`src/model/knowledge_extractor.py::save_video_knowledge`
格式对应 `VideoKnowledge` dataclass:

```json
{
  "bvid": "...", "title": "...",
  "summary": "...",
  "core_views": [],
  "key_concepts": [],
  "topics": [],
  "arguments": [{"claim": "...", "evidence": "..."}],
  "mental_model_hints": [{"hint": "...", "context": "..."}],
  "decision_examples": [{"scenario": "...", "reasoning": "...", "conclusion": "..."}],
  "expression_samples": ["原话1", "原话2"]
}
```

### 2.6 `data/rag_chunks/{source_id}.json` — RAG 知识块

产出者:`src/rag/chunker.py::build_chunks`
消费者:外部 RAG 系统 / `main.py::chunks`

```json
{
  "schema_version": "1.0",
  "source_id": "BOOK_戎震避坑_30450a_ch01",
  "source_type": "book_chapter",
  "source_title": "戎震避坑 - 第一章 为什么要避坑",
  "parent_id": "BOOK_戎震避坑_30450a",
  "chunks": [
    {
      "chunk_id": "BOOK_戎震避坑_30450a_ch01_chunk_00",
      "text": "正文块文本",
      "summary": "块摘要",
      "keywords": ["避坑", "筛选"],
      "char_range": [0, 1200],
      "topic_id": "BOOK_戎震避坑_30450a_ch01_topic_000",
      "metadata": {
        "chapter_index": 1,
        "chapter_title": "第一章 为什么要避坑",
        "parent_book_id": "BOOK_戎震避坑_30450a",
        "topic_title": "筛选机制",
        "chunk_index": 0,
        "overlap": 100
      }
    }
  ]
}
```

切分约束：

- 优先按 `cleaned.topics` 切块
- 单 topic 超长时按 `target_size` 二次切分，并保留 `overlap`
- `summary` 优先复用对应 `knowledge.summary`
- `keywords` 优先复用 `knowledge.key_concepts`，缺失时退化为 topic tags

### 2.7 `data/knowledge/blogger_profile.json` — 博主画像(终态)

产出者:`src/model/knowledge_extractor.py::merge_knowledge`
消费者:`src/generate/skill_generator.py`

对应 `BloggerProfile` dataclass。**v0.3 起升级到女娲/张雪峰.skill 格式对齐版**,字段分组如下:

| 分组 | 字段 | 说明 |
|---|---|---|
| 基础身份 | `name`, `uid`, `domain`, `self_intro` | |
| **引言新增** | `signature_quote`, `core_philosophy` | 文首大金句 + 核心理念段 |
| **身份卡** | `identity_who`, `identity_origin`, `identity_now` | 第一人称三段式 |
| 心智模型 | `mental_models: [{name, one_liner, evidence[], application, limitation}]` | **结构升级** |
| 决策启发式 | `decision_heuristics: [{rule, scenario, case}]` | **结构升级** |
| 表达 | `style`, `signature_phrases`, `expression_dna` | `expression_dna` 升为 **7 维度**:`sentence_style / vocabulary / rhythm / humor / certainty / citation_habit / debate_strategy`(老的 `opening_patterns` 等仍兼容) |
| 价值观三层 | `values_pursued`, `values_rejected`, `inner_tensions` | 拆分 |
| 边界 | `anti_patterns`, `honest_boundaries`, `knowledge_boundary` | |
| **时间线与谱系** | `timeline: [{time, event, impact}]`, `influenced_by`, `influenced_who` | 新增 |
| 示例与溯源 | `typical_qa_pairs`, `sources`, `video_sources`, `key_quotes`, `research_date` | `sources` 为新主字段，`video_sources` 保留兼容 |
| 旧兼容 | `core_views`, `values` | 老版产物可继续加载 |

**向后兼容**:模板对所有新字段都用 `{% if %}` 守护,老 `blogger_profile.json` 能正常渲染(对应节块自动隐藏)。要享受新格式,重跑阶段 4(`python main.py model`)。

### 2.8 `output/{name}.skill.md`

最终产物。Jinja2 模板 `templates/skill.md.j2` 渲染。结构包括 YAML frontmatter、效果示例、触发方式、心智模型/判断准则/价值观/表达 DNA、素材来源、诚实边界、关于博主。

---

## 3. 模块详解

### 3.1 `main.py` — CLI 入口

- 5 个子命令 + 1 个 `run`(编排 1-5)。
- `parse_stages()`:解析 `1,2,3` / `3-5` / `all` 语法,返回 `[int]`。
- `run` 用 `click.Context.invoke()` 复用其他 subcommand,避免重复实现。
- 每个子命令**延迟 import** 子模块:避免 CLI 启动即加载 torch/funasr(慢)。

### 3.2 `src/config.py` — 配置中心

- **读取**:`load_dotenv()` → `os.getenv()` → 构造 Pydantic 模型。
- **校验**:目前仅 `default=""`,字段为空 → 运行时按需检查(如 `clean` 阶段发现 api_key 为空时退化为规则模式)。
- **路径派生**:`audio_dir`、`transcripts_dir` 等全部通过 `@property` 从 `data_dir` 派生。修改目录结构时改 `config.py` 一处即可。
- **`ensure_dirs()`**:每次 `load_config()` 调用,自动 mkdir。

### 3.3 `src/crawl/video_list.py`

- **异步**:使用 `bilibili_api` 的 async API,`run_crawl()` 用 `asyncio.run()` 包成同步。
- **分页**:`ps=30`,每页间 `await asyncio.sleep(3 + random.uniform(0, 3))` 避风控。
- **412 风控**:指数退避 `10s → 20s`,最多 3 次。
- **增量**:调用方传入 `existing_bvids` 集合,命中的跳过。
- **合并**:新旧视频按 `bvid` 作为 key 合并保存,保留历史记录。

### 3.4 `src/crawl/audio_download.py`

- `yt-dlp` 作为**子进程**调用(`subprocess.run`,`timeout=300`)。
- Cookies 以 Netscape 格式临时文件传递(`generate_cookies_file`)。
- **完整性判断**:`wave` 模块读取实际时长,与 `duration` 字符串(MM:SS / HH:MM:SS)对比,容差 30s。
- **force 模式**:`force=True` 会先删旧文件再下载(付费视频补全场景)。
- 下载失败返回 `None`,**不计入配额**(主命令中 `quota` 仅在 `path` 非空时递增)。

### 3.5 `src/crawl/auth.py`

B站认证模块,提供统一的凭据获取入口,三级策略:

1. **`.env` 手动配置**(`config.bilibili.sessdata` 非空) — 向后兼容,直接构建 `Credential`
2. **缓存文件**(`data/.credentials.json`) — 读取后调用 `get_self_info()` 验证有效性
3. **二维码扫码登录** — 终端打印 QR 码,轮询 `check_state()`,登录成功后自动缓存

关键函数:
- `get_credential(config) -> (Credential, buvid3)`:统一入口,上层只调这一个
- `run_qrcode_login() -> (Credential, buvid3)`:同步包装的二维码登录
- `save_credential(credential, buvid3, path)`:序列化到 JSON
- `load_cached_credential(path) -> Optional[(Credential, buvid3)]`:从缓存读取
- `is_credential_valid(credential) -> bool`:调用轻量 API 验证

缓存 JSON schema:
```json
{ "sessdata": "", "bili_jct": "", "dedeuserid": "", "buvid3": "", "ac_time_value": "", "saved_at": "ISO8601" }
```

CLI 命令:`python main.py login` — 强制扫码登录并保存凭证。

### 3.7 `src/asr/funasr_engine.py`

- **设备三级回退**(MPS 支持已加入):
  ```python
  if torch.cuda.is_available(): device = "cuda:0"
  elif torch.backends.mps.is_available(): device = "mps"
  else: device = "cpu"
  ```
- **模型缓存本地化**:通过 `MODELSCOPE_CACHE` / `MS_CACHE_HOME` 环境变量指向 `data/.cache/modelscope/`,避免污染 `~/.cache`。
- **VAD 切片**:`max_single_segment_time=60000`(ms),防止极长音频无法处理。
- **OOM 重试**:`batch_size_s=300 → 60`,对 CUDA 与 MPS 都触发(`self._use_cuda or self._use_mps`)。
- **时间戳**:优先用 `sentence_info`(句级),降级用整块 `timestamp`(`[[start_ms, end_ms], ...]`)。
- **`check_transcript_integrity()`**:4 项检查 — 文件存在 / JSON 合法 / full_text 非空 / 音频时长-转写覆盖时长 < 60s。

### 3.8 `src/clean/text_processor.py`

两块职责:

**a. LLM 客户端工厂 `create_llm_client(provider, config)`**

- 返回 `LLMClient` Protocol 的实现(`ClaudeLLMClient` / `OpenAILLMClient`)。
- `qwen` / `deepseek` / `ollama` 都复用 `OpenAILLMClient`(它们全部兼容 OpenAI Chat Completions 协议)。
- `api_key` 缺失或 SDK 初始化失败时返回 `None`,下游代码必须处理 None(降级为规则模式)。

**b. 文本处理器 `TextProcessor`**

- `remove_filler_words()`:正则去中文填充词,合并空格,去重复标点。
- `merge_short_segments(min_length=10)`:把字数 < 10 的片段合并到前一段。
- `segment_by_topic()`:LLM 调用(最多 8000 字),失败降级为"全文"单段。

### 3.9 `src/model/knowledge_extractor.py`

- **两个 Prompt**:
  - `VIDEO_KNOWLEDGE_PROMPT` — 单视频 → 9 字段(原 5 + nuwa 新增 4)
  - `PROFILE_SYNTHESIS_PROMPT` — 多视频聚合 → 博主画像
- **JSON 容错 `_safe_json_loads`** 5 轮(见源码行 13-56):
  1. 直接 `json.loads`
  2. 清控制字符 + 字符串内裸换行转义
  3. 去尾随逗号
  4. 替换 `...` 占位符
  5. 截到最后合法 `}`
- **降级**:整个 `merge_knowledge` 失败时调用 `_fallback_profile`,用词频统计拼一个基础画像。
- **文本截断**:`full_text[:10000]` 喂给 LLM,多视频摘要最多取 50 个。

### 3.10 `src/generate/skill_generator.py`

- `jinja2.Environment(trim_blocks=True, lstrip_blocks=True)`:去掉模板语法产生的多余空行。
- 把 `BloggerProfile` 的每个字段逐个传给模板(非 `**asdict(profile)`,便于追加 `generation_date` 等派生字段)。

---

## 4. 配置系统

### 4.1 环境变量加载链

```
.env 文件
  ↓ python-dotenv 读入 os.environ
os.getenv("KEY", "default")
  ↓ load_config() 构造 Pydantic 模型
AppConfig
  ↓ 注入各阶段
```

### 4.2 新增配置项的标准流程

1. `config.py` 对应子 `BaseModel` 里加字段
2. `config.py` `load_config()` 里加 `os.getenv(...)`
3. `config.example.env` 加注释样例
4. README 的"配置参考"表格加一行
5. 本文档 [4.3 环境变量清单](#43-环境变量清单) 加一行

### 4.3 环境变量清单

| 变量 | 默认 | 用途 |
|---|---|---|
| `BILIBILI_SESSDATA` | "" | B站 Cookie,爬取阶段必需 |
| `BILIBILI_BILI_JCT` | "" | B站 Cookie |
| `BILIBILI_BUVID3` | "" | B站 Cookie |
| `UP_UID` | 0 | 目标 UP 主 UID |
| `LLM_PROVIDER` | `claude` | `claude` / `openai` / `qwen` / `deepseek` / `ollama` |
| `ANTHROPIC_API_KEY` | "" | `LLM_PROVIDER=claude` 时必填 |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Claude 模型名 |
| `OPENAI_API_KEY` | "" | `LLM_PROVIDER=openai` 时必填 |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | 兼容第三方反代 |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI 模型名 |
| `QWEN_API_KEY` | "" | DashScope API Key |
| `QWEN_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 兼容接口地址 |
| `QWEN_MODEL` | `qwen3-235b-a22b` | Qwen 模型名 |
| `DEEPSEEK_API_KEY` | "" | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek 地址 |
| `DEEPSEEK_MODEL` | `deepseek-reasoner` | R1 推理模型;长文建议换 `deepseek-chat` |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | 本地 Ollama 地址 |
| `OLLAMA_MODEL` | `qwen2.5:3b` | 本地模型名 |
| `FUNASR_MODEL` | `paraformer-zh` | ASR 主模型 |
| `FUNASR_VAD_MODEL` | `fsmn-vad` | VAD |
| `FUNASR_PUNC_MODEL` | `ct-punc` | 标点恢复 |
| `DATA_DIR` | `./data` | 中间产物根目录 |
| `OUTPUT_DIR` | `./output` | 最终产物目录 |

---

## 5. LLM 抽象层

### 5.1 统一协议

```python
class LLMClient(Protocol):
    def chat(self, prompt: str, max_tokens: int = 4096) -> str: ...
```

目前只有一个方法 `chat`,**单轮、纯文本**。

### 5.2 两种实现

| 类 | SDK | 用于 |
|---|---|---|
| `ClaudeLLMClient` | `anthropic.Anthropic` | Anthropic Claude 原生 API |
| `OpenAILLMClient` | `openai.OpenAI` | OpenAI + Qwen / DeepSeek / Ollama(OpenAI 兼容协议) |

### 5.3 新增一个 LLM 提供商

**场景 A:该提供商支持 OpenAI 兼容协议**(最常见)

只改两处:

1. `src/config.py`:加 `XxxConfig` 类 + `load_config()` 里读环境变量
2. `src/clean/text_processor.py::create_llm_client`:在 `provider_map` 里加一行

就完事了,完全复用 `OpenAILLMClient`。

**场景 B:原生 API 协议不兼容**

新增一个 `XxxLLMClient` 类,实现 `chat(prompt, max_tokens) -> str`,然后在 `create_llm_client` 里分支返回。

### 5.4 Prompt 存放位置

- `src/clean/text_processor.py` 底部:`TOPIC_SEGMENT_PROMPT`
- `src/model/knowledge_extractor.py` 底部:`VIDEO_KNOWLEDGE_PROMPT`, `PROFILE_SYNTHESIS_PROMPT`

修改时**必须保留 JSON schema 注释**,否则下游 `_safe_json_loads` 配合的字段读取会悄悄变成空数组。

---

## 6. 断点续传与完整性校验

每个阶段都有对应的 `check_xxx_integrity()` 函数,返回 `(bool, reason)`。

| 阶段 | 校验函数 | 校验项 |
|---|---|---|
| 1.音频 | `check_audio_completeness` | 文件存在 + 大小 ≥ 1KB + 时长偏差 ≤ 30s |
| 2.转写 | `check_transcript_integrity` | JSON 合法 + full_text 非空 + segments 非空 + 时长覆盖偏差 ≤ 60s |
| 3.清洗 | `check_cleaned_integrity` | JSON 合法 + full_text / topics / segments 均非空 |
| 4.知识 | `check_knowledge_integrity` | JSON 合法 + summary / core_views 非空 |

**额外规则**:
- 阶段 3 还会对比 `transcript.full_text` 与 `cleaned.full_text` 的长度比,转写更长 >10% 时重新清洗(处理"阶段2重新转写后阶段3未更新"的情形)。
- 阶段 4 的**博主画像** `blogger_profile.json` **每次都重新合成**,因为任何新视频都会影响聚合结果。

### 6.1 新增字段时的断点续传陷阱

如果你给 `cleaned` 文件加了新字段,**老文件没有该字段**。对策二选一:

- **软兼容**:读取时用 `.get("new_field", default)`
- **硬校验**:把新字段纳入 `check_cleaned_integrity`,触发老文件自动重跑

---

## 7. 错误处理与重试策略

| 场景 | 位置 | 策略 |
|---|---|---|
| B站 412 风控 | `video_list.py::fetch_user_videos` | 指数退避 10s/20s,最多 3 次 |
| yt-dlp 下载失败 | `audio_download.py::download_audio` | 内部 `--retries 3`;失败返回 `None`,不计配额 |
| yt-dlp 超时 | 同上 | `subprocess.run(timeout=300)` |
| CUDA/MPS OOM | `funasr_engine.py::_generate_with_oom_retry` | `empty_cache()` → `batch_size_s=60` 重试 1 次 |
| LLM 返回非法 JSON | `knowledge_extractor.py::_safe_json_loads` | 5 轮修复 |
| LLM 调用异常 | `merge_knowledge` | try/except,降级为规则生成画像 |
| api_key 未配置 | `create_llm_client` | 返回 `None`,调用方降级 |

**设计原则:失败不中断批处理**。单个视频失败时打印 `[red]`,循环继续,最后显示 `成功 N/M`。

---

## 8. 扩展点

### 8.1 替换 ASR 引擎(如用 Whisper)

改动最小面:

1. 新增 `src/asr/whisper_engine.py`,提供等价的 `transcribe(audio_path, bvid) -> TranscriptResult`
2. `main.py::asr` 里根据配置切换
3. `TranscriptResult` dataclass 保持不变,下游无感

### 8.2 新增数据源(如 YouTube)

1. `src/crawl/` 下新增 `youtube_list.py`、`youtube_download.py`
2. 产出物必须对齐 `data/video_list.json` schema(至少 `bvid`/`title`/`duration`;可以把 `bvid` 字段重用成 video_id)
3. 音频文件仍按 `{id}.wav` 存进 `data/audio/`
4. 后续阶段无需改

### 8.3 新增输出格式(如 Notion 页面)

1. `src/generate/` 下新增 `notion_generator.py`
2. 输入仍是 `BloggerProfile`
3. `main.py` 加一个 `export` 子命令

### 8.4 自定义 Prompt / 更换知识框架

两种方式:

- **微调**:直接改 `VIDEO_KNOWLEDGE_PROMPT` / `PROFILE_SYNTHESIS_PROMPT` 中的字段说明
- **整体替换**:新建 `src/model/xxx_extractor.py`,产出新的 `BloggerProfile` 子集,然后写新的 `templates/xxx.md.j2`

### 8.5 并行化

当前单进程串行。并行化建议:

- 阶段1 下载:改 `audio_download` 用 `asyncio` + `aiohttp`,或 `concurrent.futures.ThreadPoolExecutor`。
- 阶段2 ASR:**不建议并行**(GPU 显存会 OOM)。
- 阶段3/4 LLM 调用:可以 `asyncio.gather` 批量请求,注意限流。

---

## 9. 常见修改场景手册

### 9.1 改变一个阶段的输出目录

**只改 `src/config.py`**,所有 `@property` 集中在那里。不要在业务代码里硬编码路径。

### 9.2 添加一个新的 LLM 模型(已有供应商)

改 `.env` 的 `XXX_MODEL` 即可,代码无需改动。

### 9.3 添加一个新的 BloggerProfile 字段

1. `src/model/knowledge_extractor.py::BloggerProfile` 加字段(dataclass)
2. `PROFILE_SYNTHESIS_PROMPT` 的 JSON schema 里加字段说明
3. `merge_knowledge()` 里 `BloggerProfile(...)` 构造时 `data.get("new_field", default)`
4. `templates/skill.md.j2` 加 `{% if new_field %}...{% endif %}` 块
5. `skill_generator.py::generate()` 的 `template.render()` 参数里加该字段
6. **无需改 `load_blogger_profile`**:它已经用白名单 `__dataclass_fields__` 过滤

### 9.4 让某个视频被"强制重跑"

删对应阶段的产物文件即可:

```bash
rm data/transcripts/BVxxxxx.json   # 强制重跑 asr + clean + model
rm data/cleaned/BVxxxxx.json       # 强制重跑 clean + model
rm data/knowledge/BVxxxxx.json     # 强制重跑 model
rm data/knowledge/blogger_profile.json  # 强制重跑画像合成(model 阶段每次都会重跑)
```

### 9.5 在 Mac 上临时强制用 CPU(例如 MPS 出现兼容性问题)

不必改代码,临时传入 `device`:

```python
engine = FunASREngine(model_dir=config.model_cache_dir, device="cpu")
```

或者在 `main.py::asr` 加一个 `--device` CLI 选项。

### 9.6 切换 FunASR 模型

改 `.env` 的 `FUNASR_MODEL`(同名模型需 ModelScope 上可下载)。首次运行会自动下载到 `data/.cache/modelscope/`。

### 9.7 调整知识提取的"风格化"程度

改 `VIDEO_KNOWLEDGE_PROMPT` 中 `expression_samples` 的描述,以及 `PROFILE_SYNTHESIS_PROMPT` 中 `expression_dna` 的示例。提高 `max_tokens` 也能让 LLM 有更多空间写细节。

---

## 10. 开发规范

### 10.1 代码风格

- **类型注解**:公共函数必须有 `-> ReturnType`,参数尽量加。
- **docstring**:至少写一段中文说明 + `Args/Returns`(参考现有代码)。
- **日志**:用 `rich.console.Console()`,按颜色区分:
  - `[blue]`:进行中的步骤
  - `[green]`:成功
  - `[yellow]`:警告/跳过
  - `[red]`:错误
  - `[dim]`:细节
- **不要引入 `print`**:全部用 `console.print`。

### 10.2 导入规范

- **延迟导入重库**:`torch`/`funasr`/`anthropic`/`openai` 只在真正用到时 `import`,避免 CLI 启动 slow。
- 看 `main.py` 每个子命令开头的 `from X import Y`。

### 10.3 文件产出规范

- 一律 `json.dump(..., ensure_ascii=False, indent=2)`。
- 写文件前 `output_dir.mkdir(parents=True, exist_ok=True)`。
- 写入**新文件/完整文件**,不要做部分追加(简化断点续传)。

### 10.4 提交前检查清单

- [ ] 新加配置是否同步改了 `config.py` + `config.example.env` + README + 本文档
- [ ] 修改的数据契约是否更新了 [§2 数据流与契约](#2-数据流与契约)
- [ ] 老产物文件是否会被新代码正确重跑(或做软兼容)
- [ ] `main.py` 的 `--help` 输出还清晰吗

---

## 11. 调试与排查

### 11.1 单阶段运行

```bash
# 只跑知识提取(常用于迭代 Prompt)
python main.py model --llm deepseek

# 只跑生成(调模板时反复运行,< 1s)
python main.py generate
```

### 11.2 小样本测试

```bash
# 只抓 3 个视频,跑完全流程
python main.py run --uid 12345678 --max-videos 3
```

### 11.3 查看某阶段中间产物

```bash
# 人类可读的 JSON
python -m json.tool data/knowledge/BVxxxxx.json
python -m json.tool data/knowledge/blogger_profile.json | less
```

### 11.4 常见错误与排查

| 症状 | 可能原因 | 排查 |
|---|---|---|
| `触发B站风控(412)` 反复出现 | Cookie 过期 / IP 被限 | 重新获取 Cookie;换网络或等待 |
| 下载成功但音频时长明显不足 | 付费视频 + 无大会员 Cookie | 开通会员后 `crawl` 阶段会自动重下 |
| `检测到 Apple Silicon,使用 MPS 加速` 后卡住 | MPS 某些算子未实现 | 设 `PYTORCH_ENABLE_MPS_FALLBACK=1` 或 `device="cpu"` |
| JSON 解析 5 轮全失败 | LLM 输出被系统截断 / 非常不规范 | 检查 `max_tokens` 是否过小;换模型(DeepSeek R1 > chat > Qwen turbo 稳定性) |
| 画像合成时某字段为空 | LLM 没按 schema 返回 | 在 Prompt 里加具体示例;或改用输出 JSON mode 的模型 |
| `ModuleNotFoundError: No module named 'torch'` | funasr 未声明 torch 硬依赖 | `pip install torch torchaudio` |

### 11.5 看 FunASR 到底做了什么

FunASR 自身日志很详尽,`rich` 输出不会压过它。如果怀疑模型未加载,看 stdout 的 `Loading...` 字样。

---

## 12. 已知限制与 TODO

**已知限制**:

- 单进程串行,百视频级规模需要 1-3 小时(主要在 ASR)。
- `subtitle.py` 已实现但未被主流程调用;若 UP 主有官方字幕,本可跳过 ASR。
- `_fallback_profile` 非常简陋,只适合应急。
- LLM Prompt 的上下文长度硬编码(10000 / 8000),长文可能被截断。
- 默认 DeepSeek 模型是 `deepseek-reasoner`,对长文本价格偏高;生产环境建议换 `deepseek-chat`。
- `anthropic_model` 默认值 `claude-sonnet-4-20250514` 是硬编码,升级模型需改 `.env`。

**TODO 候选**:

- [ ] 主流程对接 `subtitle.py`,有官方字幕时优先使用
- [ ] 阶段 3/4 支持 `asyncio.gather` 并发 LLM 请求
- [ ] 增加 `--device` CLI 选项覆盖自动检测
- [ ] 增加 `--dry-run` 查看每个阶段将要处理哪些文件
- [ ] 单元测试:特别是 `_safe_json_loads`、`check_*_integrity`、`parse_stages`
- [ ] Web UI / Gradio 界面

---

## 13. 变更记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-04-15 | 初始 | 文档创建 |
| 2026-04-15 | 初始 | 增加 Apple Silicon MPS 加速支持(`src/asr/funasr_engine.py`:`_use_mps` 标志、`_free_gpu_cache` 统一 CUDA/MPS 缓存释放) |
| 2026-04-16 | 初始 | **自动登录**:新增 `src/crawl/auth.py` 认证模块(二维码扫码登录 + 凭证缓存),新增 `login` CLI 命令,`crawl` 命令自动获取凭据(三级策略:.env > 缓存 > 扫码),`generate_cookies_file` 改为接收 Credential 对象 |
| 2026-04-15 | 初始 | **SKILL.md 模板全面对齐 [女娲.skill](https://github.com/alchaincyf/nuwa-skill) / [张雪峰.skill](https://github.com/alchaincyf/zhangxuefeng-skill) 风格**:`BloggerProfile` 新增 13 字段(`signature_quote`/`core_philosophy`/`identity_*`/`values_pursued`/`values_rejected`/`inner_tensions`/`timeline`/`influenced_by`/`influenced_who`/`key_quotes`/`research_date`);`mental_models` 和 `decision_heuristics` 结构升级(前者加 `one_liner`/`evidence[]`/`limitation`,后者加 `scenario`/`case`);`expression_dna` 扩展为 7 维度;`PROFILE_SYNTHESIS_PROMPT` 全面改写(强制 evidence≥3、case、inner_tensions);`templates/skill.md.j2` 新增"角色扮演规则"、"Agentic Protocol"、"身份卡"、"人物时间线"、"智识谱系"、"关键引用"节;`examples/sample.skill.md` 替换为虚构人物「老鹰」完整示范;`merge_knowledge` 的 `max_tokens` 从 8192 提升至 12288 |
| 2026-04-22 | AITCX08 | **v0.3 大版本**:`src/reader/` 章节模块化（`generate_book_id`/`chapter_to_cleaned`/`book_to_chapter_cleaneds`，章节正则≥3 命中按章节切，否则 5000-8000 字硬切兜底）；新增 `src/rag/chunker.py`（`build_chunks` 输出 `data/rag_chunks/{source_id}.json` 标准 chunks，schema_version 1.0，含 `source_type/chapter/char_range/keywords/topic_id`）；`main.py` 新增 `fuse`（多 glob 收集 cleaned 合成统一画像）+ `chunks`（独立重建 RAG chunks）；`distill` 加 `--by-chapter/--rag-chunks` 双开关；`BloggerProfile` 新增 `sources: list[dict]` 字段（带 `source_type`），`video_sources` 兼容保留；`PROFILE_SYNTHESIS_PROMPT` summaries 标注 `[视频]/[书章节]`；`_extract_json_payload` 新增（剥 `<think>` 标签 + `\`\`\`json` 代码块 + 括号计数找首个完整平衡块，应对 DeepSeek-R1 / 第三方代理输出）；`templates/skill.md.j2` 修复 jinja2 `lstrip_blocks` 导致的 frontmatter 缩进 bug（避免 GitHub 渲染 YAML 报错）；新增 104 单测覆盖 R1 兼容 + JSON 7 轮修复 + 章节切分 + chunks 切片 + BloggerProfile schema 兼容 |
| 2026-06-04 | feat/meeting-minutes-stage1 | 新增 `src/meeting/` 模块与 `meeting` 命令（飞书风格会议纪要 MD/PDF），新增 markdown/weasyprint 依赖，复用 read_document/create_llm_client/_safe_json_loads |
| 2026-04-23 | AITCX08 | **磁盘节省工作流 + 时间戳输出 + JSON 7 轮**：`main.py::asr` 新增 `--delete-audio/--keep-audio`（默认删，转写 + 完整性双校验通过后 `unlink`）+ `--watch/--watch-interval N`（持续监听 audio 目录，FunASR 引擎只加载一次，Ctrl+C 优雅退出）；抽出 `_scan_pending_audios` / `_process_pending_batch` 两个 helper 给单次和 watch 模式共用；`crawl` 把 `data/transcripts/{bvid}.json` 完整的 BV 算入 `complete_bvids`，避免被 ASR 删音频后重复下载；推荐双终端工作流（`crawl` + `asr --watch`）让 1266 视频场景磁盘占用从 ~40GB 降至 ~3-5GB。`_safe_json_loads` 升至 7 轮（新增第 5 轮 Python/JS 字面量替换 `None/NaN/Infinity/undefined → null`、`True/False → true/false`，新增第 6 轮缺值补 `null`：`"key":, → "key":null,`，应对长上下文 LLM "Expecting value" 抖动）；`extract_from_video / merge_knowledge` 失败自动 `_dump_llm_failure` 落盘原始 content + Prompt 到 `data/llm_debug/`；`output/{name}.skill.md` 改为 `output/{name}-{YYYYMMDD-HHMMSS}.skill.md`（每次新增不覆盖，3 处生成点统一用 `_skill_output_path` helper）；新增 7 用例 `tests/test_asr_pipeline.py`（共 111 单测）|
| 2026-06-05 | feat/meeting-minutes-stage2 | **阶段二音频路径**：`FunASREngine.__init__` 新增 `spk_model: Optional[str] = None`（传 `"cam++"` 启用说话人分离），`TranscriptSegment` 新增 `speaker: str = ""`，解析逻辑提取为模块级纯函数 `_parse_result_segments` / `_spk_label`；新增 `src/meeting/audio_transcriber.py`（`convert_to_wav16k` ffmpeg 转码 + `audio_to_transcript` FunASR cam++ + `_merge_consecutive` 相邻同说话人合并 + `_fmt_ts` 时间戳格式化）；`main.py::meeting` 新增 `_AUDIO_SUFFIXES` 按后缀分流音频/txt，音频路径产出带「说话人 N」标注的 MD+PDF；现有 `asr` 命令行为不变 |
| 2026-08-12 | AITCX08 | **v0.4 多平台与本地 Dashboard**：新增 `src/platforms/`（Bilibili/抖音 Source Adapter + 显式 registry）、`src/application/`（命令/查询/revision/events/job lease）、`src/distillation/`（3/1/3 受控引擎、原子状态、双 ETA）、`src/outputs/`（episodes/skill/rag 可组合输出）、`src/orchestration/`（`TaskManager` + 无窗口 Worker + 资源闸门 + SQLite 租约）、`src/dashboard/`（FastAPI + SSE + 会话/CSRF/Origin 校验 + React 作战台）；跨平台主键统一为 `<platform>_<item_id>`；新增「1.6 编排、Worker 与本地 Dashboard」章节 |
| 2026-08-13 | AITCX08 | **开源发布加固**：`src.outputs` / `src.platforms` / `src.distillation` 三包改 PEP 562 惰性导出，修复 `pytest tests/outputs` 的循环导入崩溃；`engine` 改用 `creator_record()` 写 `job_state.json`，`SourceCreator.raw_metadata`（mappingproxy）不再参与 `asdict`/持久化；`_default_pid_probe` 捕获 `psutil.Error`，非正常关闭后 Dashboard 可重新启动；新增 `tests/test_import_order.py` 与引擎/编排回归用例，补齐 `pyproject.toml`（pytest/ruff/coverage）与 Windows CI |
