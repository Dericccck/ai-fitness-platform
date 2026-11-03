import type { ChatResponse, ChatRun } from "./types";

export class ChatRunCancelled extends Error {
  constructor() { super("回复已停止"); this.name = "ChatRunCancelled"; }
}

export function waitForRunDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(signal.reason); return; }
    const onAbort = () => {
      clearTimeout(timer); signal?.removeEventListener("abort", onAbort); reject(signal?.reason);
    };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", onAbort); resolve(); }, milliseconds);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export class ChatRunPending extends Error {
  constructor() { super("结果暂时无法确认，请重试查询当前任务"); this.name = "ChatRunPending"; }
}

export class ChatRunTerminalError extends Error {
  readonly run: ChatRun;
  constructor(run: ChatRun) { super("本次处理未完成，请先核对业务结果后再发起新操作"); this.name = "ChatRunTerminalError"; this.run = run; }
}

export function retryableRunQuery(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  if ("status" in error) return [0,408,429,502,503,504].includes(Number(error.status));
  return error.name === "TimeoutError" || error instanceof TypeError;
}

export async function waitForChatRun(
  getRun: () => Promise<ChatRun>, signal?: AbortSignal,
  pause: (ms: number) => Promise<void> = ms => waitForRunDelay(ms, signal),
  timeout = 300_000,
  options: { now?: () => number; random?: () => number; onRun?: (run: ChatRun) => void } = {},
): Promise<ChatResponse> {
  const now = options.now ?? Date.now, random = options.random ?? Math.random;
  const deadline = now() + timeout;
  let failures = 0;
  while (now() < deadline) {
    signal?.throwIfAborted();
    let run: ChatRun;
    try { run = await getRun(); }
    catch (error) {
      if (signal?.aborted) throw signal.reason;
      if (!retryableRunQuery(error)) throw error;
      const retryAfter = error instanceof Error && "retryAfterMs" in error ? Number(error.retryAfterMs) : 0;
      const delay = retryAfter > 0 ? retryAfter : Math.min(8000,1000 * 2 ** Math.min(failures++,3)) * (0.8+random()*0.4);
      await pause(Math.min(delay,Math.max(0,deadline-now())));
      continue;
    }
    failures = 0;
    options.onRun?.(run);
    if (run.status === "COMPLETED" && run.response) return run.response;
    if (run.status === "CANCELLED") throw new ChatRunCancelled();
    if (!["RUNNING", "CANCEL_REQUESTED"].includes(run.status)) throw new ChatRunTerminalError(run);
    await pause(Math.min(1000,Math.max(0,deadline-now())));
  }
  throw new ChatRunPending();
}

export type PendingChat = { version: 2; runId: string; conversationId: string; cancelRequestId: string; stopRequested: boolean };
type ChatStorage = Pick<Storage,"getItem"|"setItem"|"removeItem">;
export function readPendingChat(storage: ChatStorage): PendingChat | null {
  const raw = storage.getItem("fitness.pending-chat");
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value.runId !== "string" || !value.runId || typeof value.conversationId !== "string" || !value.conversationId) throw new Error("invalid");
    const pending: PendingChat = {version:2,runId:value.runId,conversationId:value.conversationId,
      cancelRequestId: typeof value.cancelRequestId === "string" && value.cancelRequestId ? value.cancelRequestId : crypto.randomUUID(),
      stopRequested:value.stopRequested === true};
    storage.setItem("fitness.pending-chat",JSON.stringify(pending));
    return pending;
  } catch { storage.removeItem("fitness.pending-chat"); return null; }
}
export function savePendingChat(storage: ChatStorage, pending: PendingChat): void {
  const previous = readPendingChat(storage);
  if (previous && previous.runId !== pending.runId) throw new ChatRunPending();
  storage.setItem("fitness.pending-chat",JSON.stringify(pending));
}
export function clearPendingChat(storage: ChatStorage, runId: string): void {
  if (readPendingChat(storage)?.runId === runId) storage.removeItem("fitness.pending-chat");
}

export async function cancelRegisteredRun(
  cancel: () => Promise<ChatRun>, isSubmitting: () => boolean,
  signal?: AbortSignal,
  pause: () => Promise<void> = () => waitForRunDelay(250, signal),
): Promise<ChatRun> {
  const deadline = Date.now() + 15_000;
  while (true) {
    signal?.throwIfAborted();
    try { return await cancel(); }
    catch (error) {
      // 停止可早于 POST 的任务登记；只重试取消请求，不重复提交对话。
      if (!(error instanceof Error) || !("status" in error) || error.status !== 404
        || !isSubmitting() || Date.now() >= deadline) throw error;
      await pause();
    }
  }
}
