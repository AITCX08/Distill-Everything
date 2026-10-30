import { useEffect, useState, type PropsWithChildren } from "react";
import { Badge, Text } from "@fluentui/react-components";
import {
  AddSquareRegular,
  BookRegular,
  DocumentRegular,
  GlobeRegular,
  HistoryRegular,
  HomeRegular,
} from "@fluentui/react-icons";
import type { WorkspaceId } from "./useWorkspace";
import { getJson } from "../api/client";
import type { HealthResponse, RuntimeModels } from "../api/schema";

const navigation: ReadonlyArray<{ id: WorkspaceId; label: string; icon: typeof HomeRegular }> = [
  { id: "mission", label: "工作台", icon: HomeRegular },
  { id: "create", label: "创建任务", icon: AddSquareRegular },
  { id: "platforms", label: "平台与登录", icon: GlobeRegular },
  { id: "history", label: "任务管理", icon: HistoryRegular },
  { id: "artifacts", label: "知识库", icon: BookRegular },
];

const UNAVAILABLE_MODELS: RuntimeModels = {
  transcription: { provider: "本地引擎", model: "暂不可用" },
  summary: { provider: "本地引擎", model: "暂不可用" },
};

function modelLabel({ provider, model }: { provider: string; model: string }): string {
  return `${provider} / ${model}`;
}

export function AppShell({ children, activeWorkspace = "mission" }: PropsWithChildren<{ activeWorkspace?: WorkspaceId }>) {
  const [models, setModels] = useState<RuntimeModels>(UNAVAILABLE_MODELS);

  useEffect(() => {
    let mounted = true;
    getJson<HealthResponse>("/api/v1/health")
      .then((health) => { if (mounted) setModels(health.models); })
      .catch(() => { if (mounted) setModels(UNAVAILABLE_MODELS); });
    return () => { mounted = false; };
  }, []);

  return <div className="dashboard-frame">
    <header className="dashboard-topbar">
      <a className="dashboard-brand" href="#mission" aria-label="Distill Everything 工作台首页">
        <span>DISTILL // EVERYTHING</span><i aria-hidden="true" /><Text>本地内容蒸馏工作台</Text>
      </a>
      <div className="dashboard-topbar__meta"><Badge appearance="outline" color="success" className="engine-state">本地引擎在线</Badge></div>
    </header>
    <aside className="dashboard-sidebar">
      <nav aria-label="主导航">
        {navigation.map((item) => {
          const Icon = item.icon;
          return <a key={item.id} href={`#${item.id}`} aria-current={item.id === activeWorkspace ? "page" : undefined}>
            <Icon aria-hidden="true" /> <span>{item.label}</span>
          </a>;
        })}
      </nav>
      <section className="status-panel" aria-label="运行状态">
        <div className="status-panel__heading"><DocumentRegular aria-hidden="true" /> <Text as="h2" size={400}>运行状态</Text></div>
        <dl>
          <div><dt>服务状态</dt><dd>运行中</dd></div>
          <div className="status-panel__models">
            <dt>模型状态</dt>
            <dd>
              <span><small>转写模型</small><strong>{modelLabel(models.transcription)}</strong></span>
              <span><small>摘要模型</small><strong>{modelLabel(models.summary)}</strong></span>
            </dd>
          </div>
          <div><dt>存储状态</dt><dd>本地优先</dd></div>
        </dl>
      </section>
    </aside>
    <main className="dashboard-main">{children}</main>
    <footer className="dashboard-footer">
      <Text><strong>开源</strong><span aria-hidden="true">·</span><strong>本地优先</strong><span aria-hidden="true">·</span><strong>隐私保护</strong></Text>
      <Text>转写在本机完成；摘要文本会发送给你配置的 LLM 提供商，选择 Ollama 可全程离线。</Text>
      <a href="https://github.com/AITCX08/Distill-Everything">项目地址</a>
      <a href="https://github.com/AITCX08/Distill-Everything#readme">文档</a>
    </footer>
  </div>;
}
