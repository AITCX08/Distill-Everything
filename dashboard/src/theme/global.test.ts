import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const globalCss = readFileSync(
  resolve(process.cwd(), "src/theme/global.css"),
  "utf8",
);

describe("dashboard sidebar layout", () => {
  it("keeps the runtime status directly below the navigation", () => {
    const statusPanelRule = globalCss.match(/\.status-panel\{([^}]*)\}/)?.[1];

    expect(statusPanelRule).toBeDefined();
    expect(statusPanelRule).not.toContain("margin-top:auto");
  });
});
