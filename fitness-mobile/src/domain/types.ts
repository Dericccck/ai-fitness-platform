export type MediaKind = "image" | "video";

export type MediaItem = {
  id: string;
  uri: string;
  kind: MediaKind;
  name: string;
  mimeType?: string;
  size?: number;
  duration?: number;
};

export type DemoRun = {
  id: string;
  question: string;
  media: MediaItem[];
  createdAt: number;
  state: "QUEUED" | "RUNNING" | "COMPLETED" | "CANCELLED";
};

export type Conversation = {
  id: string;
  createdAt: number;
  runs: DemoRun[];
};

export const DEFAULT_QUESTION =
  "请根据我上传的图片或视频分析训练动作，并说明可改进的地方。";

export function validateSelection(items: MediaItem[]): string | null {
  if (!items.length) return null;
  if (items.length > 3) return "一次最多选择 3 张图片。";
  if (items.some((item) => item.kind === "video") && items.length !== 1)
    return "一次只能选择 1 个视频，不能与图片混合。";
  for (const item of items) {
    if (item.kind === "image" && item.size && item.size > 10 * 1024 * 1024)
      return "图片不能超过 10 MB。";
    if (item.kind === "video" && item.size && item.size > 100 * 1024 * 1024)
      return "视频不能超过 100 MB。";
    if (
      item.kind === "video" &&
      item.duration &&
      (item.duration < 2000 || item.duration > 120000)
    )
      return "视频时长需在 2 秒至 2 分钟之间。";
    const allowed =
      item.kind === "image"
        ? ["image/jpeg", "image/png", "image/webp"]
        : ["video/mp4", "video/quicktime"];
    if (item.mimeType && !allowed.includes(item.mimeType))
      return "当前格式暂不支持，请选择 JPG、PNG、WebP、MP4 或 MOV。";
  }
  return null;
}

export function titleFor(conversation: Conversation): string {
  return conversation.runs[0]?.question.slice(0, 18) || "新对话";
}
