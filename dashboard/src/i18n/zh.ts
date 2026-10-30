const stageLabels: Record<string, string> = {
  created: "已创建",
  running: "执行中",
  queued: "排队中",
  enumerated: "等待处理",
  downloading: "下载中",
  downloaded: "下载完成",
  extracting_audio: "提取音频中",
  transcribing: "转写中",
  cleaning: "清洗文本中",
  extracting_knowledge: "提取知识中",
  summarizing: "生成摘要中",
  writing: "写入产物中",
  pause_requested: "正在暂停",
  paused: "已暂停",
  partial: "部分完成",
  completed: "已完成",
  failed: "失败",
  retry: "等待重试",
};

export function stageLabel(stage: string): string {
  return stageLabels[stage] ?? stage;
}

export const jobStatusLabel = stageLabel;

export function platformLabel(platform: string): string {
  return ({ bilibili: "哔哩哔哩", douyin: "抖音", youtube: "YouTube", "imported-series": "外部系列任务", auto: "自动识别" } as Record<string, string>)[platform] ?? platform;
}

export function authStatusLabel(status: string): string {
  return ({ configured: "已登录", missing: "未登录", authenticating: "登录中" } as Record<string, string>)[status] ?? status;
}

export function itemTypeLabel(itemType: string): string {
  return ({ video: "视频" } as Record<string, string>)[itemType] ?? itemType;
}

export function authMessageLabel(message: string): string {
  return message === "Scan in external Chromium" ? "请在浏览器中扫码登录" : message;
}
