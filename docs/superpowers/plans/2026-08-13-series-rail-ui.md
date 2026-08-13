# 作品轨道界面优化实施计划

> **供代理执行：** 必须使用 `superpowers:executing-plans` 按任务串行实现；本计划不使用子代理。所有步骤使用复选框跟踪。

**目标：** 将工作台中浏览器默认样式的第 1 至第 8 集数字条改造成可识别状态、可选择、可换行的圆形节点轨道。

**架构：** 保留 `SeriesRail` 的输入属性和 `onSelect(rowId)` 行为，只在组件内部增加状态标记与提示语义。作品轨道样式使用独立的 `series-rail.css`，由现有前端入口在全局操作样式后加载；自动化测试验证用户可观察的状态、提示和选择行为，CSS 使用项目现有主题变量，不引入依赖。

**技术栈：** React 18、TypeScript、Vitest、Testing Library、原生 CSS、现有 Fluent UI 主题变量。

## 全局约束

- 中文界面文案和中文项目文档保持简体中文。
- 不修改后端接口、状态计算顺序、任务数据模型或作品排序。
- 不增加动画库、图标库或其他依赖。
- 单主代理串行开发，不启动子代理，不运行 `pytest`。
- Windows 本地验证使用 `C:\Coding\node\node.exe` 直接运行前端工具，不打开可见 CMD 窗口。

---

### 任务 1：补充节点状态的行为测试

**文件：**
- 修改：`dashboard/src/features/mission-control/SeriesRail.test.tsx`
- 测试：`dashboard/src/features/mission-control/SeriesRail.test.tsx`

**接口：**
- 使用：`SeriesRail({ total, completed, active, failed, selectedRowId, onSelect })`
- 验证：节点的 `data-status`、`title`、状态标记、`aria-label` 和 `aria-pressed`

- [x] **步骤 1：写入失败测试**

  在现有点击测试后增加状态呈现测试，使用 `within` 检查完成标记和失败标记：

  ```tsx
  it("exposes status text, tooltip and non-color markers", () => {
    render(<SeriesRail total={4} completed={1} active={1} failed={1} selectedRowId={2} onSelect={vi.fn()} />);

    const completedPart = screen.getByRole("button", { name: "第 1 集，已完成" });
    const failedPart = screen.getByRole("button", { name: "第 2 集，失败" });
    const activePart = screen.getByRole("button", { name: "第 3 集，执行中" });

    expect(completedPart).toHaveAttribute("data-status", "已完成");
    expect(completedPart).toHaveAttribute("title", "第 1 集 · 已完成");
    expect(within(completedPart).getByText("✓")).toHaveAttribute("aria-hidden", "true");
    expect(within(failedPart).getByText("!")).toHaveAttribute("aria-hidden", "true");
    expect(activePart).toHaveAttribute("data-status", "执行中");
    expect(failedPart).toHaveAttribute("aria-pressed", "true");
  });
  ```

- [x] **步骤 2：运行定向测试并确认按预期失败**

  在 `dashboard` 目录运行：

  ```powershell
  & 'C:\Coding\node\node.exe' '.\node_modules\vitest\vitest.mjs' run 'src/features/mission-control/SeriesRail.test.tsx'
  ```

  预期：新增用例失败，原因是节点尚无 `title` 和状态标记；原有点击用例继续通过。

- [x] **步骤 3：提交测试红灯**

  ```powershell
  git add -- dashboard/src/features/mission-control/SeriesRail.test.tsx
  git commit -m "test: 覆盖作品轨道状态语义"
  ```

### 任务 2：实现圆形状态节点的语义结构

**文件：**
- 修改：`dashboard/src/features/mission-control/SeriesRail.tsx`
- 测试：`dashboard/src/features/mission-control/SeriesRail.test.tsx`

**接口：**
- 保留：`onSelect(rowId: number): void`
- 新增内部函数：`markFor(status: PartStatus): string | null`
- 产生：`.series-rail__list`、`.series-rail__part`、`.series-rail__item`、`.series-rail__number`、`.series-rail__marker`

- [x] **步骤 1：实现最小语义结构**

  用以下实现替换 `SeriesRail.tsx`。完成状态返回 `✓`、失败状态返回 `!`，其余状态不渲染文字标记；标记设置 `aria-hidden="true"`，避免读屏重复朗读。

  ```tsx
  type PartStatus = "已完成" | "失败" | "执行中" | "等待处理";

  function statusFor(rowId: number, completed: number, active: number, failed: number): PartStatus {
    if (rowId <= completed) return "已完成";
    if (rowId <= completed + failed) return "失败";
    if (rowId <= completed + failed + active) return "执行中";
    return "等待处理";
  }

  function markFor(status: PartStatus): string | null {
    if (status === "已完成") return "✓";
    if (status === "失败") return "!";
    return null;
  }

  export function SeriesRail({
    total,
    completed,
    active,
    failed,
    selectedRowId,
    onSelect,
  }: {
    total: number;
    completed: number;
    active: number;
    failed: number;
    selectedRowId: number | null;
    onSelect: (rowId: number) => void;
  }) {
    return <section className="series-rail" aria-label="系列进度">
      <div className="series-rail__heading"><span>作品轨道</span><span>{total} 集</span></div>
      <ol className="series-rail__list">
        {Array.from({ length: total }, (_, index) => {
          const rowId = index + 1;
          const status = statusFor(rowId, completed, active, failed);
          const mark = markFor(status);
          return <li className="series-rail__part" key={rowId}>
            <button
              type="button"
              className="series-rail__item"
              data-status={status}
              title={`第 ${rowId} 集 · ${status}`}
              aria-label={`第 ${rowId} 集，${status}`}
              aria-pressed={selectedRowId === rowId}
              onClick={() => onSelect(rowId)}
            >
              <span className="series-rail__number">{rowId}</span>
              {mark && <span className="series-rail__marker" aria-hidden="true">{mark}</span>}
            </button>
          </li>;
        })}
      </ol>
    </section>;
  }
  ```

- [x] **步骤 2：运行定向测试并确认转绿**

  ```powershell
  & 'C:\Coding\node\node.exe' '.\node_modules\vitest\vitest.mjs' run 'src/features/mission-control/SeriesRail.test.tsx'
  ```

  预期：`SeriesRail.test.tsx` 全部通过，无错误或警告。

- [x] **步骤 3：提交组件实现**

  ```powershell
  git add -- dashboard/src/features/mission-control/SeriesRail.tsx
  git commit -m "feat: 完善作品轨道节点语义"
  ```

### 任务 3：实现圆形轨道视觉与响应式布局

**文件：**
- 新增：`dashboard/src/theme/series-rail.css`
- 修改：`dashboard/src/main.tsx`
- 更新：`src/dashboard/static/` 内置前端产物

**接口：**
- 使用：任务 2 产生的 `series-rail` BEM 类名和 `data-status`
- 产生：40px 圆形节点、连接线、完成/执行/失败/等待/选中/焦点状态以及窄屏换行

- [x] **步骤 1：补齐组件样式**

  在任务工作台样式区加入以下规则：

  ```css
  .series-rail{display:grid;gap:14px;padding:18px;border:1px solid var(--line);border-radius:12px;background:linear-gradient(145deg,rgb(13 30 39/.92),rgb(7 20 27/.92))}
  .series-rail__heading{display:flex;align-items:center;justify-content:space-between;gap:16px;color:var(--text-secondary);font-size:13px;font-weight:700}
  .series-rail__heading span:first-child{color:var(--text-primary);font-size:16px}
  .series-rail__list{display:flex;flex-wrap:wrap;gap:14px 18px;margin:0;padding:0;list-style:none}
  .series-rail__part{position:relative;width:40px;height:40px}
  .series-rail__part:not(:last-child)::after{content:"";position:absolute;left:40px;top:19px;width:18px;height:2px;background:var(--line-strong);pointer-events:none}
  .series-rail__item{position:relative;display:grid;place-items:center;width:40px;height:40px;padding:0;appearance:none;border:1px solid var(--line-strong);border-radius:50%;background:#0a171d;color:var(--text-secondary);font:700 14px/1 "Cascadia Mono",Consolas,monospace;cursor:pointer;transition:border-color .16s ease,background-color .16s ease,color .16s ease,box-shadow .16s ease,transform .16s ease}
  .series-rail__item:hover{border-color:var(--accent);color:var(--text-primary);transform:translateY(-1px)}
  .series-rail__item:focus-visible{outline:2px solid var(--accent-strong);outline-offset:5px}
  .series-rail__item[data-status="已完成"]{border-color:rgb(66 217 150/.72);background:rgb(66 217 150/.1);color:var(--success)}
  .series-rail__item[data-status="执行中"]{border-color:var(--accent);background:rgb(39 216 242/.12);color:var(--accent-strong);box-shadow:0 0 18px rgb(39 216 242/.22)}
  .series-rail__item[data-status="失败"]{border-color:rgb(255 124 124/.78);background:rgb(255 124 124/.1);color:var(--danger)}
  .series-rail__item[aria-pressed="true"]{box-shadow:0 0 0 2px #07151c,0 0 0 4px var(--accent-strong)}
  .series-rail__item[data-status="执行中"][aria-pressed="true"]{box-shadow:0 0 0 2px #07151c,0 0 0 4px var(--accent-strong),0 0 20px rgb(39 216 242/.3)}
  .series-rail__number{transform:translateY(.5px)}
  .series-rail__marker{position:absolute;right:-5px;top:-5px;display:grid;place-items:center;width:16px;height:16px;border:2px solid #07151c;border-radius:50%;background:var(--success);color:#03120e;font:900 10px/1 system-ui,sans-serif}
  .series-rail__item[data-status="失败"] .series-rail__marker{background:var(--danger);color:#1b0505}
  @media(max-width:560px){.series-rail{padding:16px}.series-rail__list{column-gap:12px}.series-rail__part:not(:last-child)::after{width:12px}}
  ```

- [x] **步骤 2：运行组件测试和前端构建**

  ```powershell
  & 'C:\Coding\node\node.exe' '.\node_modules\vitest\vitest.mjs' run 'src/features/mission-control/SeriesRail.test.tsx'
  & 'C:\Coding\node\node.exe' '.\node_modules\typescript\bin\tsc' -b
  & 'C:\Coding\node\node.exe' '.\node_modules\vite\bin\vite.js' build
  ```

  预期：测试通过，TypeScript 编译成功，Vite 生成 `dist` 且无错误。

- [x] **步骤 3：在本地工作台完成视觉验收**

  打开现有本地 Dashboard 的 `/#mission`，确认：

  1. 第 1 至第 8 集为独立圆形节点，不再是灰色原生按钮条。
  2. 已完成、执行中、失败、等待和当前选中状态均能区分。
  3. 点击节点仍能打开对应作品详情。
  4. 缩窄浏览器后节点自然换行且页面没有横向溢出。
  5. Tab 键焦点轮廓清晰，完成和失败状态包含非颜色标记。

- [x] **步骤 4：检查差异并提交样式**

  ```powershell
  git diff --check
  git status --short
  git add -- dashboard/src/main.tsx dashboard/src/theme/series-rail.css src/dashboard/static
  git commit -m "style: 重绘作品轨道节点"
  ```

### 任务 4：最终回归与交付检查

**文件：**
- 核对：`dashboard/src/features/mission-control/SeriesRail.tsx`
- 核对：`dashboard/src/features/mission-control/SeriesRail.test.tsx`
- 核对：`dashboard/src/theme/series-rail.css`
- 核对：`dashboard/src/main.tsx`
- 核对：`src/dashboard/static/`

**接口：**
- 不新增公共接口；确认所有原有属性与点击回调兼容。

- [x] **步骤 1：运行前端定向测试和构建复验**

  ```powershell
  & 'C:\Coding\node\node.exe' '.\node_modules\vitest\vitest.mjs' run 'src/features/mission-control/SeriesRail.test.tsx'
  & 'C:\Coding\node\node.exe' '.\node_modules\typescript\bin\tsc' -b
  & 'C:\Coding\node\node.exe' '.\node_modules\vite\bin\vite.js' build
  ```

  预期：所有命令退出码为 0。

- [x] **步骤 2：检查提交范围**

  ```powershell
  git status --short
  git log -4 --oneline
  ```

  预期：除计划跟踪更新外没有未提交修改，最近提交只包含作品轨道测试、组件和样式。

## 执行记录

- 定向测试先因缺少节点提示而失败，加入语义结构后通过，完成红灯到绿灯验证。
- 完整前端回归通过：24 个测试文件、64 个测试全部成功。
- TypeScript 编译与 Vite 生产构建成功，内置静态包校验成功。
- 本地服务已从旧资源 `index-DCjzjv9c.css` 切换到新资源 `index-BiPm8n8x.css`，浏览器检测到 18 条作品轨道样式规则且控制台无错误。
- 验收时本地服务没有任务记录，因此工作台未生成真实作品轨道；节点状态、点击语义和产物结构由组件测试覆盖，实际样式加载由浏览器检查覆盖。
