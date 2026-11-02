import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Spinner, Text } from "@fluentui/react-components";
import { ArrowSyncRegular } from "@fluentui/react-icons";
import { getJson, postJson } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";

type Platform = "bilibili" | "douyin" | "xiaohongshu";

type Entry = { key: string; label: string; available: boolean; reason: string };
type Folder = { id: string; title: string; count: number };
type Item = {
  item_id: string;
  title: string;
  url: string;
  cover: string | null;
  duration: number | null;
  published_at: string | null;
};

const PLATFORMS: ReadonlyArray<{ id: Platform; label: string }> = [
  { id: "bilibili", label: "哔哩哔哩" },
  { id: "douyin", label: "抖音" },
  { id: "xiaohongshu", label: "小红书" },
];

function formatDuration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "—";
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

export function LibraryPage() {
  const [platform, setPlatform] = useState<Platform>("bilibili");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [account, setAccount] = useState<string | null>(null);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folder, setFolder] = useState("");
  const [activeKind, setActiveKind] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const loadCapabilities = useCallback(async (target: Platform) => {
    setLoading(true);
    setMessage("");
    try {
      const payload = await getJson<{ entries: Entry[]; account: string | null }>(
        `/api/v1/library/${target}`,
      );
      setEntries(payload.entries);
      setAccount(payload.account);
      const first = payload.entries.find((entry) => entry.available);
      setActiveKind(first?.key ?? "");
      setFolder("");
      setItems([]);
      setSelected(new Set());
      if (target === "bilibili") {
        const folderPayload = await getJson<{ folders: Folder[] }>(
          `/api/v1/library/bilibili/folders`,
        );
        setFolders(folderPayload.folders);
      } else {
        setFolders([]);
      }
    } catch {
      setEntries([]);
      setMessage("读取内容库失败，请确认本地引擎仍在运行。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadCapabilities(platform); }, [loadCapabilities, platform]);

  const needsFolder = platform === "bilibili" && activeKind === "favorites" && !folder;

  const loadItems = useCallback(async () => {
    if (!activeKind) return;
    if (platform === "bilibili" && activeKind === "favorites" && !folder) {
      setItems([]);
      setMessage("");
      return;
    }
    setLoading(true);
    setMessage("");
    try {
      const query = new URLSearchParams({ kind: activeKind });
      if (folder) query.set("folder", folder);
      const payload = await getJson<{ items: Item[] }>(
        `/api/v1/library/${platform}/items?${query.toString()}`,
      );
      setItems(payload.items);
    } catch {
      setItems([]);
      setMessage("读取条目失败。可能是登录态过期，请重新登录后刷新。");
    } finally {
      setLoading(false);
    }
  }, [activeKind, folder, platform]);

  useEffect(() => { void loadItems(); }, [loadItems]);

  const activeEntry = useMemo(
    () => entries.find((entry) => entry.key === activeKind) ?? null,
    [activeKind, entries],
  );

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function startBatch() {
    if (selected.size === 0) return;
    setBusy(true);
    setMessage("");
    try {
      const targets = items
        .filter((item) => selected.has(item.item_id))
        .map((item) => item.url);
      const payload = await postJson<{ job_ids: string[] }>(
        `/api/v1/library/${platform}/jobs`,
        { targets, outputs: ["episodes", "skill"] },
      );
      setMessage(`已创建 ${payload.job_ids.length} 个蒸馏任务，可在「任务管理」查看进度。`);
      setSelected(new Set());
    } catch {
      setMessage("创建任务失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="workspace-page" id="library">
      <PageHeader
        title="内容库"
        description="登录后读取你自己账号里的发布、喜欢与收藏，勾选后直接蒸馏。"
        actions={
          <Button
            appearance="secondary"
            icon={<ArrowSyncRegular />}
            onClick={() => void loadCapabilities(platform)}
            disabled={loading}
          >
            刷新
          </Button>
        }
      />

      <div className="segments segments--row" role="tablist" aria-label="平台">
        {PLATFORMS.map((item) => (
          <button
            key={item.id}
            role="tab"
            className="segment"
            aria-pressed={item.id === platform}
            onClick={() => setPlatform(item.id)}
          >
            <span>{item.label}</span>
          </button>
        ))}
      </div>

      {account && (
        <Text className="num" style={{ color: "var(--mute)", fontSize: 12, marginBottom: 12, display: "block" }}>
          当前账号 {account}
        </Text>
      )}

      <div className="library">
        <aside className="library__column" aria-label="内容分类">
          <div className="segments">
            {entries.map((entry) => (
              <button
                key={entry.key}
                className="segment"
                aria-pressed={entry.key === activeKind}
                disabled={!entry.available}
                title={entry.reason || entry.label}
                onClick={() => { setActiveKind(entry.key); setSelected(new Set()); }}
              >
                <span>
                  {entry.label}
                  {!entry.available && entry.reason ? <small>{entry.reason}</small> : null}
                </span>
              </button>
            ))}
          </div>

          {folders.length > 0 && (
            <div className="folder-list">
              {folders.map((entry) => (
                <button
                  key={entry.id}
                  className="folder"
                  aria-pressed={entry.id === folder}
                  onClick={() => { setFolder(entry.id); setSelected(new Set()); }}
                >
                  <span>{entry.title}</span>
                  <span className="count">{entry.count}</span>
                </button>
              ))}
            </div>
          )}
        </aside>

        <div>
          <div className="library__grid-head">
            <h2>{activeEntry?.label ?? "选择分组"}</h2>
            <span className="hint">{items.length > 0 ? `共 ${items.length} 条` : " "}</span>
          </div>

          {loading && <Spinner label="正在读取…" />}

          {!loading && items.length === 0 && (
            <div className="library__empty">
              <h3>{needsFolder ? "先选一个收藏夹" : "这里还没有内容"}</h3>
              <p>
                {needsFolder
                  ? "左侧列出了你的收藏夹，点一个即可加载里面的视频。"
                  : "先到「平台与登录」完成扫码，回来点刷新。"}
              </p>
            </div>
          )}

          {items.length > 0 && (
            <div className="item-grid">
              {items.map((item) => (
                <button
                  key={item.item_id}
                  className="item-card"
                  aria-pressed={selected.has(item.item_id)}
                  onClick={() => toggle(item.item_id)}
                >
                  <span className="item-card__tick" aria-hidden="true">✓</span>
                  {item.cover
                    ? <img className="item-card__cover" src={`/api/v1/library/cover?url=${encodeURIComponent(item.cover)}`} alt="" loading="lazy" />
                    : <div className="item-card__cover item-card__cover--empty">无封面</div>}
                  <div className="item-card__body">
                    <p className="item-card__title">{item.title}</p>
                    <div className="item-card__meta">
                      <span>{formatDuration(item.duration)}</span>
                      <span>{item.item_id}</span>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}

          {items.length > 0 && <div className="batch-bar">
            <span className="batch-bar__count">
              已选 <b>{selected.size}</b> 项
            </span>
            <Text size={200} style={{ color: "var(--mute)" }}>
              输出：剧集拆解 + 技能文件
            </Text>
            <span className="batch-bar__spacer" />
            <Button
              appearance="secondary"
              onClick={() => setSelected(new Set(items.map((item) => item.item_id)))}
              disabled={items.length === 0}
            >
              全选本页
            </Button>
            <Button
              appearance="primary"
              onClick={() => void startBatch()}
              disabled={selected.size === 0 || busy}
            >
              {busy ? "正在创建…" : "开始蒸馏"}
            </Button>
          </div>}

          {message && <Text role="status" style={{ display: "block", marginTop: 12 }}>{message}</Text>}
        </div>
      </div>
    </section>
  );
}
