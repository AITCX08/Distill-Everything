# docs/superpowers：开发计划与设计归档

这个目录保存的是项目从 v0.3 到 v0.4 的开发过程资料（设计规格、实施计划、迁移与验收记录），由 AI 协作工作流生成并归档。

阅读时请注意：

- **这是历史记录，不是当前契约**。当前有效的架构与数据契约见仓库根目录 `DEVELOPMENT.md`；对外用法见 `README.md`。
- 文档中的 `Distill-Anyone` 是本项目改名前的旧名称（仓库也曾叫 `AITCX08/Distill-Anyone`），保留原文以保持迁移记录可追溯。
- 文档里的 `<workspace>`、`<node>`、`<existing-conda-env>` 是原文中的本机绝对路径占位符，已做脱敏；它们不代表仓库结构。
- 计划文档中的复选框反映**当时**的完成状态，可能与当前代码有出入；以代码与 `CHANGELOG.md` 为准。

想了解 v0.4 的分层设计，建议直接读：

- `DEVELOPMENT.md` 第 1.5 节（跨平台分层）与第 1.6 节（编排、Worker 与本地 Dashboard）
- `docs/superpowers/specs/2026-07-21-multi-platform-distillation-design.md`
- `docs/superpowers/specs/2026-08-10-dashboard-creator-workbench-design.md`
