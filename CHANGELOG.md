# 更新日志

本项目采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，并遵循语义化版本号。

## [0.4.0] - 2026-08-13

首个公开 Beta。核心变化是把「单机脚本流水线」升级为「多平台 + 受控 Worker + 本地 Dashboard」。

### 新增

- Source Adapter 平台层与 episodes、Skill、RAG 分块三类输出层。
- 受控 Worker：每个作品在独立子进程中完成下载、音频、转写、摘要和交付，可暂停、恢复与重试。
- 本地 Dashboard：展示脱敏状态、真实阶段、任务队列、产物预览和平台登录入口。
- Linux、Windows、macOS 基础 Python CI 与 Node 24 Dashboard CI，另加 Ruff 与 CodeQL 静态检查。
- Windows 与 macOS 的安装、启动、排障及安全边界文档。

### 变更

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
