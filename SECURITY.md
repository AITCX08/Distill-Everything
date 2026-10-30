# 安全策略

## 支持范围

| 版本 | 安全修复 |
| --- | --- |
| `0.4.x`（当前 Beta） | 接收并处理 |
| `< 0.4` | 不再维护，请先升级 |

## 本地边界

Distill-Everything 的 Dashboard 只监听 `127.0.0.1`，不提供 `--host`、局域网或公网绑定模式。请勿通过端口转发、反向代理、frp 或共享网络把它暴露给其他设备——它的会话与 CSRF 校验假设“能连上这个端口的人就是本机用户”。

平台登录凭据、Cookie、二维码载荷、浏览器 profile 与 LLM API Key 只应保存在你自己的设备上：

- 凭据只写入 `data/`（已被 `.gitignore` 忽略），不进入 Dashboard 页面、SSE、事件库或日志；
- B 站登录走外部扫码，页面只接收登录状态；
- 抖音使用持久化 Chromium profile（默认 `data/browser/douyin`），该目录同样不应提交或打包分发。

## 报告漏洞

**请不要公开创建 issue。** 使用仓库的私密报告通道：

- GitHub Security Advisories：<https://github.com/AITCX08/Distill-Everything/security/advisories/new>
- 备用邮箱：<flashw008@gmail.com>（主题前缀 `[SECURITY]`）

请在报告中提供：

- 受影响版本或提交号；
- 受影响组件、最短复现步骤；
- 影响说明与建议修复方向；
- 完全脱敏的错误摘要与请求/响应片段。

**报告中不要附带**：Cookie、`SESSDATA`、`bili_jct`、二维码内容、浏览器 profile、媒体原文件、真实任务标题、绝对路径、完整 Worker 日志或 API Key。

本项目是个人维护的开源项目，不承诺固定响应时限或 SLA；维护者会在确认问题后协调修复与公开披露，并默认在修复版本发布后于 Advisory 中致谢报告者（可要求匿名）。

## 已知设计取舍

- **数据会离开本机**：选择云端 LLM 提供商（Claude / OpenAI / Qwen / DeepSeek）时，转写文本会发送到对应端点；FunASR 首次运行会从 ModelScope 下载模型权重。需要完全离线请使用 `LLM_PROVIDER=ollama`。
- **平台抓取**：适配器只处理你有权访问的公开内容，登录态由你本人提供；项目不内置任何绕过平台风控或付费墙的能力。
- **依赖供应链**：`requirements.txt` 使用下限约束，`dashboard/package-lock.json` 锁定前端依赖；生产使用建议自行锁定并审计依赖。
