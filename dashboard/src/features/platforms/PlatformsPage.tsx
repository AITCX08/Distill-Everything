import { useEffect, useState } from "react";
import {
  Button,
  Card,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Spinner,
  Text,
} from "@fluentui/react-components";

import { DashboardRequestError, getJson, postJson } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { StatusPill, type StatusTone } from "../../components/StatusPill";
import { authMessageLabel, authStatusLabel, itemTypeLabel, platformLabel } from "../../i18n/zh";

type Platform = {
  name: string;
  item_types: string[];
  requires_browser: boolean;
  requires_auth: boolean;
  qr_login: boolean;
  auth_status: string;
  auth_message: string;
};

type QrLogin = {
  operation_id: string;
  platform: string;
  status: string;
  message: string;
  qr_url: string;
};

type QrLoginStatus = Pick<QrLogin, "operation_id" | "status" | "message">;

function isPlatform(value: unknown): value is Platform {
  return typeof value === "object" && value !== null
    && "name" in value && typeof value.name === "string"
    && "item_types" in value && Array.isArray(value.item_types)
    && "requires_browser" in value && typeof value.requires_browser === "boolean"
    && "requires_auth" in value && typeof value.requires_auth === "boolean"
    && "qr_login" in value && typeof value.qr_login === "boolean"
    && "auth_status" in value && typeof value.auth_status === "string"
    && "auth_message" in value && typeof value.auth_message === "string";
}

function isQrLogin(value: unknown): value is QrLogin {
  return typeof value === "object" && value !== null
    && "operation_id" in value && typeof value.operation_id === "string"
    && "platform" in value && typeof value.platform === "string"
    && "status" in value && typeof value.status === "string"
    && "message" in value && typeof value.message === "string"
    && "qr_url" in value && typeof value.qr_url === "string";
}

function isQrLoginStatus(value: unknown): value is QrLoginStatus {
  return typeof value === "object" && value !== null
    && "operation_id" in value && typeof value.operation_id === "string"
    && "status" in value && typeof value.status === "string"
    && "message" in value && typeof value.message === "string";
}

function isActiveLogin(status: string): boolean {
  return status === "preparing_qr" || status === "waiting_for_scan" || status === "waiting_for_confirmation";
}

function displayAuthMessage(platform: Platform): string {
  if (platform.name === "bilibili" && platform.auth_message === "Run the Bilibili login command") {
    return "在本页面弹出二维码，扫码并在手机上确认后会自动更新登录状态。";
  }
  return authMessageLabel(platform.auth_message);
}

function authTone(status: string): StatusTone {
  if (status === "configured" || status === "authenticated") return "success";
  if (status === "expired" || status === "failed") return "danger";
  if (status === "missing" || status === "unauthenticated") return "warning";
  if (status === "checking" || status === "pending") return "active";
  return "waiting";
}

export function PlatformsPage() {
  const [platforms, setPlatforms] = useState<Platform[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loginPending, setLoginPending] = useState<string | null>(null);
  const [openingPlatform, setOpeningPlatform] = useState<string | null>(null);
  const [qrLogin, setQrLogin] = useState<QrLogin | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const result = await getJson<unknown>("/api/v1/platforms");
      if (!Array.isArray(result) || !result.every(isPlatform)) throw new Error("invalid platforms response");
      setPlatforms(result);
    } catch {
      setPlatforms(null);
      setError("无法读取平台登录状态，请确认本地引擎仍在运行。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  useEffect(() => {
    if (!qrLogin || !isActiveLogin(qrLogin.status)) return undefined;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const response = await getJson<unknown>(
          `/api/v1/platforms/${encodeURIComponent(qrLogin.platform)}/login/${qrLogin.operation_id}`,
        );
        if (stopped || !isQrLoginStatus(response)) return;
        setQrLogin((current) => current && current.operation_id === response.operation_id
          ? { ...current, status: response.status, message: response.message }
          : current);
        if (response.status === "succeeded") {
          setQrLogin(null);
          void refresh();
          return;
        }
        if (isActiveLogin(response.status)) timer = setTimeout(() => void poll(), 1000);
      } catch {
        if (!stopped) setQrLogin((current) => current ? {
          ...current,
          status: "failed",
          message: "无法读取登录状态，请关闭后重新发起登录。",
        } : current);
      }
    };
    void poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [qrLogin?.platform, qrLogin?.operation_id, qrLogin?.status]);

  async function openLogin(platform: Platform) {
    setLoginPending(platform.name);
    setOpeningPlatform(null);
    setError(null);
    try {
      const response = await postJson<unknown>(`/api/v1/platforms/${encodeURIComponent(platform.name)}/login`, {});
      if (isQrLogin(response)) {
        setQrLogin(response);
        return;
      }
      if (typeof response === "object" && response !== null && "status" in response && response.status === "opening_browser") {
        setOpeningPlatform(platform.name);
        return;
      }
      throw new Error("invalid login response");
    } catch (reason) {
      setError(reason instanceof DashboardRequestError && reason.code === "offline"
        ? "本地引擎离线，未能启动登录。"
        : "本地引擎尚未确认登录请求，请重试。");
    } finally {
      setLoginPending(null);
    }
  }

  return (
    <section id="platforms" className="workspace-page platforms-page" aria-label="平台与登录">
      <PageHeader
        title="平台与登录"
        description="登录凭据仅由本地引擎保存和使用，页面不会显示或存储凭据内容。"
        actions={<Button appearance="secondary" onClick={() => void refresh()} disabled={loading}>
          {loading ? "正在刷新…" : "刷新状态"}
        </Button>}
      />
      <Card>
        {error && <Text role="alert">{error}</Text>}
        {platforms?.length === 0 && <Text role="status">尚未配置可用的平台适配器。</Text>}
        <div className="platform-status-grid">
          {platforms?.map((platform) => (
          <section className="platform-status-card" key={platform.name} aria-label={`${platformLabel(platform.name)} 登录状态`}>
            <div className="platform-status-card__heading">
              <Text as="h2" size={500}>{platformLabel(platform.name)}</Text>
              <StatusPill label={authStatusLabel(platform.auth_status)} tone={authTone(platform.auth_status)} />
            </div>
            <Text className="metric">支持 {platform.item_types.map(itemTypeLabel).join("、") || "未声明类型"}</Text>
            <Text>{displayAuthMessage(platform)}</Text>
            {platform.requires_auth && platform.qr_login && <Button
              appearance="primary"
              onClick={() => void openLogin(platform)}
              disabled={loginPending !== null}
            >
              {loginPending === platform.name ? "正在启动登录…" : `登录 ${platformLabel(platform.name)}`}
            </Button>}
            {openingPlatform === platform.name && <Text role="status">登录窗口已在外部浏览器打开。完成扫码或确认后，请刷新平台状态。</Text>}
          </section>
          ))}
        </div>
      </Card>

      <Dialog open={qrLogin !== null} onOpenChange={(_, data) => { if (!data.open) setQrLogin(null); }}>
        <DialogSurface aria-label={`扫描二维码登录${qrLogin ? platformLabel(qrLogin.platform) : ""}`}>
          <DialogBody>
            <DialogTitle>扫描二维码登录{qrLogin ? platformLabel(qrLogin.platform) : ""}</DialogTitle>
            <DialogContent>
              {qrLogin && isActiveLogin(qrLogin.status) && (
                qrLogin.status === "preparing_qr"
                  ? <Spinner label="正在生成二维码…" />
                  : <img className="bilibili-qr" src={qrLogin.qr_url} alt={`${platformLabel(qrLogin.platform)}登录二维码`} />
              )}
              <Text role="status">{qrLogin?.message}</Text>
              {qrLogin && isActiveLogin(qrLogin.status) && (
                <Text size={200}>请使用手机 App 扫码，并在手机上确认；成功后本页会自动更新为已登录。</Text>
              )}
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" onClick={() => setQrLogin(null)}>关闭</Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </section>
  );
}
