import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const getJson = vi.hoisted(() => vi.fn());
const postJson = vi.hoisted(() => vi.fn());

vi.mock("../../api/client", () => ({
  getJson,
  postJson,
  DashboardRequestError: class DashboardRequestError extends Error {},
}));

import { PlatformsPage } from "./PlatformsPage";

const bilibili = {
  name: "bilibili",
  item_types: ["video"],
  requires_browser: true,
  requires_auth: true,
  auth_status: "missing",
  auth_message: "Run the Bilibili login command",
  qr_login: true,
};

afterEach(() => {
  cleanup();
  getJson.mockReset();
  postJson.mockReset();
});

describe("PlatformsPage", () => {
  it("presents each platform as a named login status card", async () => {
    getJson.mockResolvedValueOnce([bilibili]);

    render(<PlatformsPage />);

    expect(await screen.findByRole("region", { name: "哔哩哔哩 登录状态" })).toHaveTextContent("未登录");
  });

  it("shows Bilibili's QR code inside a dialog and polls its local login session", async () => {
    getJson.mockResolvedValueOnce([bilibili]).mockResolvedValue({
      operation_id: "bili-op",
      status: "waiting_for_confirmation",
      message: "已扫码，请在手机上确认登录。",
    });
    postJson.mockResolvedValueOnce({
      operation_id: "bili-op",
      platform: "bilibili",
      status: "waiting_for_scan",
      message: "请使用哔哩哔哩 App 扫码",
      qr_url: "/api/v1/platforms/bilibili/login/bili-op/qr",
    });

    render(<PlatformsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "登录 哔哩哔哩" }));

    expect(await screen.findByRole("dialog", { name: "扫描二维码登录哔哩哔哩" })).toBeVisible();
    expect(screen.getByRole("img", { name: "哔哩哔哩登录二维码" })).toHaveAttribute(
      "src",
      "/api/v1/platforms/bilibili/login/bili-op/qr",
    );
    expect(await screen.findByText("已扫码，请在手机上确认登录。")).toBeVisible();
    await waitFor(() => expect(getJson).toHaveBeenCalledWith("/api/v1/platforms/bilibili/login/bili-op"));
  });

  it("closes the Bilibili QR dialog as soon as the login session succeeds", async () => {
    let completeLoginStatus: (value: unknown) => void = () => undefined;
    const pendingLoginStatus = new Promise<unknown>((resolve) => { completeLoginStatus = resolve; });
    getJson.mockResolvedValueOnce([bilibili]).mockReturnValueOnce(pendingLoginStatus).mockResolvedValueOnce([
      { ...bilibili, auth_status: "configured" },
    ]);
    postJson.mockResolvedValueOnce({
      operation_id: "bili-op",
      platform: "bilibili",
      status: "waiting_for_scan",
      message: "请使用哔哩哔哩 App 扫码",
      qr_url: "/api/v1/platforms/bilibili/login/bili-op/qr",
    });

    render(<PlatformsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "登录 哔哩哔哩" }));

    expect(await screen.findByRole("dialog", { name: "扫描二维码登录哔哩哔哩" })).toBeVisible();
    completeLoginStatus({
      operation_id: "bili-op",
      status: "succeeded",
      message: "登录成功，凭据已安全保存到本机。",
    });

    await waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
    expect(getJson).toHaveBeenCalledWith("/api/v1/platforms");
  });
});
