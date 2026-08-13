import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    status: "ok",
    api_version: "v1",
    static_compatible: true,
    models: {
      transcription: { provider: "FunASR", model: "paraformer-zh" },
      summary: { provider: "DeepSeek", model: "deepseek-chat" },
    },
  }), { status: 200 })));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AppShell", () => {
  it("renders the unified workbench navigation, local engine state, and privacy footer", () => {
    render(<AppShell><main>content</main></AppShell>);

    expect(screen.getByText("DISTILL // EVERYTHING")).toBeVisible();
    expect(screen.getByText("本地内容蒸馏工作台")).toBeVisible();
    expect(screen.getByRole("navigation", { name: "主导航" })).toHaveTextContent("工作台");
    expect(screen.getByText("本地引擎在线")).toBeVisible();
    expect(screen.getByRole("link", { name: "创建任务" })).toBeVisible();
    expect(screen.getByRole("contentinfo")).toHaveTextContent("本地优先");
  });

  it("keeps creator workbench navigation readable on a narrow viewport", () => {
    render(<AppShell activeWorkspace="create"><main>内容</main></AppShell>);

    expect(screen.getByRole("navigation", { name: "主导航" })).toBeVisible();
    expect(screen.getByRole("link", { name: "创建任务" })).toHaveAttribute("aria-current", "page");
  });

  it("shows the current transcription and summary models from the local engine", async () => {
    render(<AppShell><main>内容</main></AppShell>);

    const status = screen.getByRole("region", { name: "运行状态" });
    expect(await screen.findByText("FunASR / paraformer-zh")).toBeVisible();
    expect(screen.getByText("DeepSeek / deepseek-chat")).toBeVisible();
    expect(status).toHaveTextContent("转写模型");
    expect(status).toHaveTextContent("摘要模型");
    expect(status).not.toHaveTextContent("由任务按需加载");
  });
});
