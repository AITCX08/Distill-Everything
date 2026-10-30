# Dashboard 统一工作台实施计划

> **供 Goal 执行：** 严格使用单主代理串行执行本计划。每张任务卡都按“检查 → 红灯测试 → 最小实现 → 绿灯验证 → 小提交 → 勾选”完成；不启用子代理、不并发编辑、不并行执行任务卡。

**目标：** 将 Dashboard 的五个既有页面统一为中文、深色青蓝、本地优先的内容蒸馏工作台，同时保持现有哈希路由和全部业务接口行为不变。

**架构：** 先建立不请求 API 的展示层组件和全局页面框架，再逐页把既有页面组合进新框架。页面继续负责请求、SSE 和任务控制；共享组件仅接收展示数据、可访问名称和回调，避免视觉改版侵入系列运行器、登录或产物接口。

**技术栈：** React 18、TypeScript、Fluent UI React v9、Vite、Vitest、Testing Library、现有 FastAPI 静态 Dashboard 构建脚本。

## 全局约束

- 以 [设计规格](../specs/2026-08-12-dashboard-unified-workbench-design.md) 为唯一视觉与范围依据；所有新增 Markdown 文档使用简体中文。
- 只在唯一保留的项目目录 `<workspace>\Distill-Everything` 中修改；不得访问或重建已删除的旧运行目录、旧主目录或额外工作树，也不得改动 Conda 环境。
- 不改变 `#mission`、`#create`、`#platforms`、`#history`、`#artifacts`，不改变 API 路径、请求体、登录轮询、任务暂停/恢复/取消/重试和打开本地目录行为。
- 保留 Fluent UI React v9 和现有 `@fluentui/react-icons`；不增加第二套 UI 框架、不使用伪造文件上传或无效按钮。
- 新增可见文案全部中文；作品标题优先，BV 号/来源编号、保存位置与完成时间作为次级信息。禁止将真实 Cookie、二维码内容、令牌、真实用户目录或真实任务内容写入代码、测试或截图。
- 无障碍：保留语义 `nav`、`main`、`aria-current`、表单标签、状态文本和 `:focus-visible`；支持窄屏导航与 `prefers-reduced-motion`。
- Windows 不得直接执行 `pytest` 或 `python -m pytest`。本计划前端验证使用固定 `<node>\node.exe`；Dashboard 健康检查只访问已运行的 `http://127.0.0.1:8765/api/v1/health`，不得打开可见 CMD、任务计划程序或浏览器。
- 每张任务卡结束前运行 `git diff --check`，只暂存本卡文件并使用计划指定的小提交信息。完成后更新本计划复选框；若中断，下一次从第一个未勾选任务卡继续。

---

## 文件结构与责任

| 文件 | 责任 |
| --- | --- |
| `dashboard/src/app/AppShell.tsx` | 顶部栏、图标侧栏、运行状态、内容槽和页脚 |
| `dashboard/src/app/AppShell.test.tsx` | 全局导航、可访问名称和框架文案测试 |
| `dashboard/src/components/PageHeader.tsx` | 各页面统一标题、说明和可选操作槽 |
| `dashboard/src/components/StatusPill.tsx` | 带文字的状态呈现，不依赖颜色 |
| `dashboard/src/components/ProgressSummary.tsx` | 总体进度、计数和阶段展示 |
| `dashboard/src/components/QueueTable.tsx` | 桌面队列表格及移动端项目卡 |
| `dashboard/src/components/*.test.tsx` | 共享展示组件最小交互与无障碍测试 |
| `dashboard/src/theme/global.css` | 全局颜色令牌、框架、响应式和焦点样式 |
| `dashboard/src/theme/operations.css` | 任务作战台、队列与交付面板样式 |
| `dashboard/src/features/*/*Page.tsx` | 保持请求逻辑不变，组合共享展示层并更新页面语义 |
| `dashboard/src/features/*/*.test.tsx` | 保留行为回归，增加关键中文标题/状态信息断言 |

## 任务卡

### 任务 1：建立工作台框架与视觉令牌

**文件：**
- 新建：`dashboard/src/components/PageHeader.tsx`
- 新建：`dashboard/src/components/PageHeader.test.tsx`
- 修改：`dashboard/src/app/AppShell.tsx`
- 修改：`dashboard/src/app/AppShell.test.tsx`
- 修改：`dashboard/src/theme/global.css`

**接口：**
- `PageHeader` 输入：`title: string`、`description?: string`、`actions?: ReactNode`；输出：语义化页面标题区。
- `AppShell` 继续输入 `activeWorkspace?: WorkspaceId` 和 `children`；输出：保持五个既有链接的可访问导航框架。

- [x] 1. 阅读规格、上述文件和 `dashboard/src/app/useWorkspace.ts`；确认工作树干净或仅含本计划允许的变更。
- [x] 2. 先在 `PageHeader.test.tsx` 写红灯测试：渲染 `title="创建任务"`、`description="预检来源后创建任务"` 和按钮时，断言 `h1`、描述和按钮可见。
- [x] 3. 使用 `<node>\node.exe dashboard/node_modules/vitest/vitest.mjs run dashboard/src/components/PageHeader.test.tsx` 验证红灯，预期因模块不存在失败。
- [x] 4. 新建最小实现：

```tsx
import type { ReactNode } from "react";
import { Text } from "@fluentui/react-components";

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return <header className="page-header"><div><Text as="h1" size={700}>{title}</Text>{description && <Text>{description}</Text>}</div>{actions && <div className="page-header__actions">{actions}</div>}</header>;
}
```

- [x] 5. 更新 `AppShell`：使用 Fluent 图标为五个导航项提供 `aria-hidden` 图标；增加品牌顶部栏、侧栏“运行状态”信息和安全的公开页脚链接；不得添加没有实际目的地的主题/设置按钮。
- [x] 6. 在 `global.css` 新建深色青蓝令牌与 `.dashboard-frame`、`.dashboard-sidebar`、`.dashboard-topbar`、`.page-header`、`.status-panel`、`.dashboard-footer`、窄屏横向导航和焦点样式；不移除 `prefers-reduced-motion`。
- [x] 7. 扩展 `AppShell.test.tsx`：断言导航名称仍为“主导航”、工作台链接存在、页脚含“本地优先”和当前页 `aria-current="page"`。
- [x] 8. 运行任务 1 的两个 Vitest 文件，预期全部通过；运行 `git diff --check`。
- [x] 9. 仅暂存本卡文件并提交：`feat(dashboard): add unified workbench frame`；勾选本任务卡。

### 任务 2：提取状态、进度和队列展示组件

**文件：**
- 新建：`dashboard/src/components/StatusPill.tsx`
- 新建：`dashboard/src/components/StatusPill.test.tsx`
- 新建：`dashboard/src/components/ProgressSummary.tsx`
- 新建：`dashboard/src/components/ProgressSummary.test.tsx`
- 新建：`dashboard/src/components/QueueTable.tsx`
- 新建：`dashboard/src/components/QueueTable.test.tsx`
- 修改：`dashboard/src/theme/operations.css`

**接口：**
- `StatusPill`：`label: string`、`tone: "success" | "active" | "waiting" | "warning" | "danger"`。
- `ProgressSummary`：`progress: number`、`stage: string`、`counts: { completed: number; active: number; queued: number; total: number }`。
- `QueueTable<T>`：`items: readonly T[]`、`getKey`、`renderTitle`、`renderMeta`、`renderStatus`、`renderProgress`、`renderStage`、`renderUpdated`、`renderActions`。

- [x] 1. 在三份测试中先写红灯用例：状态胶囊有可读文字；进度摘要把 `0.72` 显示为 `72%`；队列表格显示标题“公开视频：知识整理”、次行“BV1xx · 完成于 2026-08-12 10:15”和列标题“作品标题”。
- [x] 2. 运行三个测试文件，预期由于组件不存在失败。
- [x] 3. 最小实现 `StatusPill`，以 `data-tone` 提供视觉状态但始终渲染 `label`；不得仅以图标表达状态。
- [x] 4. 最小实现 `ProgressSummary`，将进度夹在 0–100，使用 Fluent `ProgressBar` 和四项中文计数，不读取接口。
- [x] 5. 最小实现 `QueueTable`：桌面使用带列标题的 `<table>`；CSS 小于 760px 时转为卡片布局，仍保留标题、次级信息、状态、进度与操作。
- [x] 6. 在 `operations.css` 写组件的成功/进行/等待/警告/失败色彩、表格行 hover、窄屏布局和 `:focus-visible`；颜色只作辅助，不覆盖文字状态。
- [x] 7. 再运行三个组件测试与 `git diff --check`，预期全部通过。
- [x] 8. 仅暂存本卡文件并提交：`feat(dashboard): add status and queue primitives`；勾选本任务卡。

### 任务 3：重构任务作战台为总览和执行队列

**文件：**
- 修改：`dashboard/src/features/mission-control/MissionControlPage.tsx`
- 修改：`dashboard/src/features/mission-control/MissionOverview.tsx`
- 修改：`dashboard/src/features/mission-control/TaskControlCard.tsx`
- 修改：`dashboard/src/features/mission-control/MissionControlPage.test.tsx`
- 修改：`dashboard/src/features/mission-control/MissionOverview.test.tsx`
- 修改：`dashboard/src/features/mission-control/TaskControlCard.test.tsx`

**接口：**
- 继续消费既有 `ProgressSnapshot`、`MissionJob`、`WorkerTask` 和现有控制回调。
- 通过 `ProgressSummary` 展示总体进度；通过 `QueueTable<WorkerTask>` 展示标题、BV 号、时间、状态、进度、阶段与动作。

- [x] 1. 在任务作战台测试先写红灯：活动任务展示作品标题、BV 号/来源编号、阶段、下载进度；完成任务展示完成时间；“技术信息”不在默认行内。
- [x] 2. 运行三个 mission 测试文件验证红灯。
- [x] 3. 把 `MissionOverview` 的指标改为组合 `ProgressSummary`；保留已完成的产物与打开保存位置操作。
- [x] 4. 将 `TaskControlCard` 的格式化标题、次级元信息、下载进度与动作作为 `QueueTable` 行渲染数据；保留暂停、恢复、取消、重试的原 API 路径和 `expected_revision`、`command_id` 请求体。
- [x] 5. 更新 `MissionControlPage` 的结构：标题区、总览、作品队列、实时日志和右侧上下文栏；没有活动任务时仍保留 0% 总览、空队列、创建任务卡与隐私/本地优先卡，不再用单张空状态卡替代首页。
- [x] 6. 运行 mission 测试和 `dashboard/src/app/App.test.tsx`；确认暂停/恢复和 SSE 测试仍通过；运行 `git diff --check`。
- [x] 7. 仅暂存本卡文件并提交：`feat(dashboard): redesign mission workbench`；勾选本任务卡。

### 任务 4：重构创建任务与平台登录页面

**文件：**
- 修改：`dashboard/src/features/create-job/CreateJobPage.tsx`
- 修改：`dashboard/src/features/create-job/CreateJobPage.test.tsx`
- 修改：`dashboard/src/features/create-job/OutputDirectoryField.tsx`
- 修改：`dashboard/src/features/create-job/OutputDirectoryField.test.tsx`
- 修改：`dashboard/src/features/platforms/PlatformsPage.tsx`
- 修改：`dashboard/src/features/platforms/PlatformsPage.test.tsx`

**接口：**
- 保持来源预检和创建的 `/api/v1/jobs/preview`、`/api/v1/jobs` 请求体不变。
- 保持平台列表、Bilibili 登录、登录状态轮询接口与自动关闭行为不变。

- [x] 1. 写红灯测试：创建页有“来源与平台”“交付内容”“保存位置”分区、模板入口和预检后创建提示；平台页有平台状态卡，Bilibili 成功后二维码对话框消失并刷新状态。
- [x] 2. 运行创建页、目录字段和平台测试验证红灯。
- [x] 3. 用 `PageHeader` 与语义化分区重组创建页；将输出卡保持为可选交付卡，模板预览继续使用现有对话框；目录字段显示“默认位置”或“本次任务覆盖位置”的清晰说明，但不改变令牌提交流程。
- [x] 4. 用统一状态卡重组平台页；Bilibili 模态框保持当前轮询逻辑，成功路径必须 `setBilibiliLogin(null)` 后刷新；抖音外部浏览器说明保留为适配器限制。
- [x] 5. 补全响应式样式类，避免在 760px 以下出现横向溢出。
- [x] 6. 运行本卡测试和 `git diff --check`，预期全部通过。
- [x] 7. 仅暂存本卡文件并提交：`feat(dashboard): unify creation and platform flows`；勾选本任务卡。

### 任务 5：重构任务管理与知识库页面

**文件：**
- 修改：`dashboard/src/features/job-history/JobHistoryPage.tsx`
- 修改：`dashboard/src/features/job-history/JobHistoryPage.test.tsx`
- 修改：`dashboard/src/features/artifacts/ArtifactsPage.tsx`
- 修改：`dashboard/src/features/artifacts/ArtifactsPage.test.tsx`

**接口：**
- 继续使用 `/api/v1/jobs`、`/items`、`/details`、`/reveal-output`、`/artifacts`、`/reveal`。
- 作品第二行格式固定为 `BV 号或来源编号 · 完成于/最后更新`；标题为第一行。

- [x] 1. 写红灯测试：历史页面任务卡显示任务标题、平台、进度、保存位置摘要；展开作品的标题第一行和 BV/完成时间第二行；默认没有“技术信息”。知识库页面显示任务交付摘要、产物标题、创建时间和打开位置操作。
- [x] 2. 运行历史和产物测试验证红灯。
- [x] 3. 用 `PageHeader`、`StatusPill` 和 `QueueTable` 或其移动端同构布局重组任务管理；不改变筛选、查看详情、打开文件夹和重试逻辑。
- [x] 4. 重组知识库为任务选择、交付摘要、产物列表与只读预览四区；保存位置只在已经选择任务并加载详情后显示。
- [x] 5. 运行本卡测试和 `git diff --check`，预期全部通过。
- [x] 6. 仅暂存本卡文件并提交：`feat(dashboard): unify history and artifact library`；勾选本任务卡。

### 任务 6：完成视觉回归、静态构建和发布交接

**文件：**
- 修改：`dashboard/src/app/App.test.tsx`（仅当需增加全页面框架回归断言）
- 修改：`README.md`（仅在现有 Dashboard 说明与最终界面不一致时，更新中文截图/入口描述）
- 修改：`docs/superpowers/plans/2026-08-12-dashboard-unified-workbench.md`

- [x] 1. 检查所有新可见中文文案；搜索 `rg -n 'Distill-Anyone|TODO|TBD' dashboard/src`，修复本轮引入的不一致或占位文案。
- [x] 2. 使用固定 Node 24 运行全部前端测试：`<node>\node.exe dashboard/node_modules/vitest/vitest.mjs run`；记录通过数和退出码。
- [x] 3. 使用固定 Node 24 运行 `<node>\node.exe dashboard/node_modules/typescript/bin/tsc -b`，随后运行 `<node>\node.exe dashboard/node_modules/vite/bin/vite.js build`。
- [x] 4. 运行 `C:\Coding\Anaconda\envs\Distill-Anyone\python.exe scripts/build_dashboard.py --from-dist` 和 `C:\Coding\Anaconda\envs\Distill-Anyone\python.exe scripts/build_dashboard.py --check`；不得执行 pytest。
- [x] 5. 访问已运行的 `http://127.0.0.1:8765/api/v1/health`，确认返回 `status: ok` 与 `static_compatible: true`；若服务未运行，仅报告现状，不以可见 CMD 启动。
- [x] 6. 使用浏览器手动核查 `#mission`、`#create`、`#platforms`、`#history`、`#artifacts` 的桌面和窄屏布局，重点确认键盘焦点、状态文字、二维码成功自动关闭和无水平溢出；不展示或截取真实二维码、Cookie、路径或任务数据。
- [x] 7. 运行 `git diff --check`、`git status --short`，确认没有 `data/`、`output/`、`.local-artifacts/` 或凭据进入暂存；更新 README（如需要）和本计划复选框。
- [x] 8. 仅暂存验收产生的文档/测试修改并提交：`docs: verify unified dashboard workbench`；勾选本任务卡。
- [x] 9. 推送当前分支，创建或更新 PR；待 CI 的 Dashboard、Linux Python 和 macOS 基础工作流全部成功后合并至 `main`。保留运行工作树，不执行 `git reset --hard`、`git clean`、`git stash drop` 或删除工作树。
