# 单目录安全收敛设计

## 目标

最终只保留 `<workspace>\Distill-Everything` 一个项目目录，同时保证本地独有提交、Dashboard 未提交修改、任务数据、输出产物和隐藏运行日志均可恢复。

## 当前状态

- `Distill-Everything` 是 Git 公共仓库和数据目录，当前本地 `main` 相对 `origin/main` 领先 27 个提交、落后 132 个提交。
- `Distill-Everything-dashboard-runtime` 是关联 worktree，当前分支为 `fix/legacy-state-cleanup`，包含尚未提交的旧系列状态清理修改，也是端口 8765 的代码运行目录。
- `Distill-Everything-main` 是关联 worktree，当前分支为 `release/main-20260812`，无未提交修改且是 `origin/main` 的祖先。
- `data/`、`output/` 与 `.local-artifacts/` 已位于最终保留目录，不迁移大文件。

## 收敛方案

1. 将旧本地 `main` 改名为归档分支，完整保存音频、会议纪要、Obsidian 和长视频覆盖提交。
2. 将 Dashboard worktree 的全部未提交修改提交到 `wip/legacy-state-cleanup`，保存未完成现场但不直接合入正式主线。
3. 在唯一目录中基于最新 `origin/main` 重建本地 `main`，并保留本设计与执行计划。
4. 停止从旧 worktree 运行的 Dashboard，把隐藏启动器改为从唯一目录加载代码，并复用唯一目录现有的数据与输出。
5. 验证新 Dashboard 健康状态后，使用 `git worktree remove` 删除两个关联 worktree；不使用手工递归删除。

## 安全约束

- 删除 worktree 前必须保证对应工作树干净，所有改动都有分支和提交引用。
- 不删除 `data/`、`output/`、`.local-artifacts/`、凭据缓存或 Git 对象。
- 不使用 `git reset --hard`、`git clean` 或强制覆盖未提交文件。
- Dashboard 切换失败时保留旧 worktree，不执行目录移除。
- 最终验证 Git 分支、工作树列表、端口 8765、健康接口和物理目录状态。

## 验收标准

- `C:\Users\Administrator\Desktop\Vibe` 下只剩一个 `Distill-Everything` 项目目录。
- 唯一目录的 `main` 以最新 `origin/main` 为基础。
- 旧本地功能提交与旧状态清理修改分别由归档分支和 WIP 分支保留。
- Dashboard 从唯一目录后台运行，端口 8765 的健康接口正常。
- 原有数据、输出和本地运行产物数量没有因收敛而减少。
