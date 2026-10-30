# 系列作品轨道视觉改造实施计划

> **供执行代理：** 严格单主代理串行执行本计划。每一步按测试先行完成；不启用子代理、不并发编辑。

**目标：** 将任务作战台的系列编号按钮组改为有状态层级、连续连接线和可读标签的作品进度轨道。

**架构：** 继续由 `SeriesRail` 根据现有计数推导每集状态，组件只新增呈现所需的内部元素和 `data-status`；CSS 负责连接线、节点层级、窄屏滚动与减弱动画，不改动任务接口或选择回调。

**技术栈：** React 18、TypeScript、Fluent UI React v9、Vitest、现有 Dashboard CSS。

## 全局约束

- 仅修改 `<workspace>\Distill-Everything-dashboard-runtime`。
- 保持 `SeriesRail` 的 props、`onSelect(rowId)`、`aria-label` 与 `aria-pressed` 行为；不改 API、SSE 或任务数据。
- 不添加依赖、不显示真实任务内容、路径、二维码或凭据。
- 测试与 TypeScript 仅使用 `<node>\node.exe`；不执行 pytest、不打开可见 CMD。

---

### 任务 1：实现连续状态轨道

**文件：**
- 修改：`dashboard/src/features/mission-control/SeriesRail.tsx`
- 修改：`dashboard/src/features/mission-control/SeriesRail.test.tsx`
- 修改：`dashboard/src/theme/operations.css`

**接口：**
- 继续接收 `total`、`completed`、`active`、`failed`、`selectedRowId` 与 `onSelect`。
- 每个节点继续输出 `button[data-status]`，并新增内部状态标记与可读“第 N 集”标签。

- [ ] 1. 在 `SeriesRail.test.tsx` 新增红灯断言：完成节点渲染可访问的“已完成”标记，选中节点带“第 3 集 · 执行中”可读标签，点击第 4 集仍调用 `onSelect(4)`。
- [ ] 2. 运行：`<node>\node.exe node_modules/vitest/vitest.mjs run src/features/mission-control/SeriesRail.test.tsx`（工作目录 `dashboard`）；预期新断言因标记和标签不存在失败。
- [ ] 3. 在 `SeriesRail.tsx` 为节点包裹轨道项目容器；按钮内部增加状态标记和序号，按钮后增加 `第 N 集` 标签；仅在选中时显示状态文本。保留现有 `aria-label`、`aria-pressed` 和点击回调。
- [ ] 4. 在 `operations.css` 新增 `.series-rail` 样式：横向连接线、圆形状态节点、完成/执行/失败/等待视觉层级、选中标签、最小 40px 命中区、窄屏横向滚动、焦点样式和 `prefers-reduced-motion` 回退。
- [ ] 5. 重跑该测试，预期通过；随后运行 `<node>\node.exe node_modules/typescript/bin/tsc -p tsconfig.json --noEmit`（工作目录 `dashboard`）和 `git diff --check`。
- [ ] 6. 仅暂存本卡文件并提交：`feat(dashboard): refine series progress rail`；勾选本任务卡。

### 任务 2：构建回归与发布交接

**文件：**
- 修改：`docs/superpowers/plans/2026-08-12-series-rail-visual.md`
- 修改：`src/dashboard/static/**`（仅由现有静态构建脚本生成）

- [ ] 1. 运行 `<node>\node.exe node_modules/vitest/vitest.mjs run`、`<node>\node.exe node_modules/vite/bin/vite.js build`（工作目录 `dashboard`）。
- [ ] 2. 运行 `C:\Coding\Anaconda\envs\Distill-Anyone\python.exe scripts/build_dashboard.py --from-dist` 与 `--check`；不得执行 pytest。
- [ ] 3. 运行 `git diff --check`、`git status --short`，确认暂存范围不含 `data/`、`output/`、`.local-artifacts/`、Cookie、二维码或用户目录。
- [ ] 4. 勾选本任务卡，仅暂存静态资源和计划文件并提交：`docs: verify series rail visual update`。
- [ ] 5. 推送当前分支，创建或更新 PR；待 Dashboard、Linux Python 和 macOS 基础 CI 成功后合并 main。
