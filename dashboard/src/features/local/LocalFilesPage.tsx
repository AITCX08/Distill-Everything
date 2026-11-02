import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Spinner, Text } from "@fluentui/react-components";
import { ArrowUploadRegular } from "@fluentui/react-icons";
import { getJson } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";

type Operation = {
  operation_id: string;
  status: "running" | "done" | "failed";
  message: string;
  file_name: string;
  outputs: { cleaned?: string; knowledge?: string; skill?: string };
};

function csrfToken(): string {
  const prefix = "distill_csrf=";
  const encoded = document.cookie.split("; ").find((cookie) => cookie.startsWith(prefix))?.slice(prefix.length);
  return encoded ? decodeURIComponent(encoded) : "";
}

async function uploadFile(file: File): Promise<Operation> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/api/v1/local", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-Distill-CSRF": csrfToken() },
    body,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload ? String(payload.detail) : "上传失败";
    throw new Error(detail);
  }
  return payload as Operation;
}

export function LocalFilesPage() {
  const [operations, setOperations] = useState<Operation[]>([]);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const track = useCallback(async (operation: Operation) => {
    setOperations((current) => [operation, ...current.filter((item) => item.operation_id !== operation.operation_id)]);
  }, []);

  useEffect(() => {
    let mounted = true;
    getJson<{ operations: Array<{ operation_id: string }> }>("/api/v1/local")
      .then(async (payload) => {
        const details = await Promise.all(
          payload.operations.slice(-5).map((item) =>
            getJson<Operation>(`/api/v1/local/${item.operation_id}`).catch(() => null)),
        );
        if (mounted) {
          setOperations(details.filter((item): item is Operation => item !== null).reverse());
        }
      })
      .catch(() => undefined);
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    const running = operations.filter((item) => item.status === "running");
    if (running.length === 0) return undefined;
    const timer = setTimeout(async () => {
      for (const item of running) {
        try {
          const fresh = await getJson<Operation>(`/api/v1/local/${item.operation_id}`);
          await track(fresh);
        } catch {
          /* 下一轮再试 */
        }
      }
    }, 1500);
    return () => clearTimeout(timer);
  }, [operations, track]);

  async function handleFiles(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    setError("");
    try {
      const operation = await uploadFile(file);
      await track(operation);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "上传失败");
    }
  }

  return (
    <section className="workspace-page" id="local">
      <PageHeader
        title="本地文件"
        description="把文档、音频或视频拖进来，本地完成解析与转写，再蒸馏成技能文件。"
        actions={
          <Button appearance="secondary" icon={<ArrowUploadRegular />} onClick={() => inputRef.current?.click()}>
            选择文件
          </Button>
        }
      />

      <div
        className={`dropzone${dragging ? " dropzone--active" : ""}`}
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void handleFiles(event.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") inputRef.current?.click(); }}
      >
        <strong>把文件拖到这里</strong>
        <span>
          文档：txt · md · html · csv · json · srt · docx · pdf · pptx · xlsx · epub　
          音视频：mp3 · m4a · wav · flac · mp4 · mkv · mov · webm …
        </span>
        <span>单个文件上限 512 MB；音视频会在本机抽音轨转写，文件不会上传到第三方。</span>
        <input
          ref={inputRef}
          type="file"
          hidden
          onChange={(event) => void handleFiles(event.target.files)}
        />
      </div>

      {error && <Text role="alert" style={{ color: "var(--danger)", marginTop: 12, display: "block" }}>{error}</Text>}

      <div className="local-list">
        {operations.map((operation) => (
          <article key={operation.operation_id} className="local-item" data-status={operation.status}>
            <div className="local-item__head">
              <span className="local-item__name">{operation.file_name}</span>
              <span className={`local-item__status local-item__status--${operation.status}`}>
                {operation.status === "running" ? "处理中" : operation.status === "done" ? "已完成" : "失败"}
              </span>
            </div>
            <p className="local-item__message">{operation.message}</p>
            {operation.status === "running" && <Spinner size="tiny" />}
            {operation.status === "done" && operation.outputs.skill && (
              <Text className="local-item__output num">产出：{operation.outputs.skill}</Text>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
