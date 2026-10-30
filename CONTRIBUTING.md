# 贡献指南

感谢你参与 Distill-Everything。请将每一项改动控制在可评审、可验证的范围内，并优先保护本地内容、平台凭据和用户隐私。

## 开发流程

1. 从最新 `main` 创建描述明确的功能分支。
2. 先补充或更新能说明预期行为的测试，再完成最小实现。
3. 在提交前运行与改动范围相符的检查，并在 PR 中写明结果。
4. 一个 PR 聚焦一个主题；涉及界面时附上完全脱敏的截图。

## 本地验证

前端使用 Node.js 24：

```powershell
cd dashboard
npm ci
node node_modules/vitest/vitest.mjs run src/app/AppShell.test.tsx src/features/mission-control/TaskControlCard.test.tsx
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
```

在 Codex Desktop 的 Windows 环境中，如果遇到可见终端窗口或长命令流不稳定，可用项目自带的后台运行器（普通终端直接 `python -m pytest` 即可）：

```powershell
$env:DISTILL_EVERYTHING_PYTHON = '<path-to-project-python.exe>'
cmd /d /c start "" /b scripts\run-pytest-background.cmd tests\dashboard\test_series_control.py -q
Get-Content .local-artifacts\test-runs\latest.exitcode
```

> 历史提示：0.4.0 之前 Windows 上的“长命令流不稳定”实际来自 `os.kill(pid, 0)`——在 Windows 上它会向整个控制台进程组发送 CTRL+C。该问题已在 `src/application/process.py` 修复，现在 Python 测试可以直接运行。

其他操作系统可使用其正常的项目 Python 测试命令；macOS 支持边界见[平台支持与故障排查](./docs/平台支持与故障排查.md)。

## 架构边界

- `src/platforms/` 仅处理平台识别、认证、枚举和下载，不应耦合 ASR、LLM 或输出格式。
- `src/outputs/` 只消费规范化产物，不能依赖平台私有字段。
- Dashboard 只展示脱敏后的本地任务状态；Cookie、二维码、API Key、浏览器 profile、原始 Worker 输出和绝对保存路径不得进入页面、SSE 或日志。
- 需要新平台时实现并注册 `PlatformAdapter`；需要新输出格式时实现并注册 `OutputTarget`。

## 不得提交的内容

不要提交 `.env`、`data/`、`output/`、浏览器 profile、Cookie、二维码、媒体文件、真实转写、真实任务状态、完整日志、密钥或用户绝对路径。示例、测试夹具和截图必须使用公开占位内容并完成脱敏。

安全问题请勿创建公开 issue，改按 [SECURITY.md](./SECURITY.md) 的方式报告。
