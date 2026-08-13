import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SeriesRail } from "./SeriesRail";

describe("SeriesRail", () => {
  it("renders one semantic control for every known series part", () => {
    const onSelect = vi.fn();
    render(<SeriesRail total={4} completed={2} active={1} failed={0} selectedRowId={3} onSelect={onSelect} />);

    expect(screen.getAllByRole("button", { name: /第 [1-4] 集/ })).toHaveLength(4);
    expect(screen.getByRole("button", { name: "第 3 集，执行中" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "第 4 集，等待处理" }));
    expect(onSelect).toHaveBeenCalledWith(4);
  });

  it("exposes status text, tooltip and non-color markers", () => {
    const view = render(<SeriesRail total={4} completed={1} active={1} failed={1} selectedRowId={2} onSelect={vi.fn()} />);
    const rail = within(view.container);

    const completedPart = rail.getByRole("button", { name: "第 1 集，已完成" });
    const failedPart = rail.getByRole("button", { name: "第 2 集，失败" });
    const activePart = rail.getByRole("button", { name: "第 3 集，执行中" });

    expect(completedPart).toHaveAttribute("data-status", "已完成");
    expect(completedPart).toHaveAttribute("title", "第 1 集 · 已完成");
    expect(within(completedPart).getByText("✓")).toHaveAttribute("aria-hidden", "true");
    expect(within(failedPart).getByText("!")).toHaveAttribute("aria-hidden", "true");
    expect(activePart).toHaveAttribute("data-status", "执行中");
    expect(failedPart).toHaveAttribute("aria-pressed", "true");
  });
});
