import type { CapabilityCatalog, ChatResponse, Confirmation } from "./types";

const baseUrl = (import.meta.env.VITE_AGENT_API_BASE_URL ?? "").replace(/\/$/, "");
const configuredContext = import.meta.env.VITE_AGENT_CONTEXT?.trim() ?? "";

export class ApiError extends Error {
  readonly status: number;
  readonly requestId: string | null;

  constructor(message: string, status: number, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
  }
}

function requestId(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function context(): string {
  if (!configuredContext) {
    throw new ApiError("未配置本地 AgentContext，请在 .env.local 中设置 VITE_AGENT_CONTEXT", 0, null);
  }
  return configuredContext;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const id = requestId();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("X-Agent-Context", context());
  headers.set("X-Request-ID", id);
  headers.set("X-Trace-ID", id);
  if (init.body) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, { ...init, headers });
  } catch {
    throw new ApiError("无法连接 Agent 服务，请确认 8090 端口服务正在运行", 0, id);
  }

  const responseRequestId = response.headers.get("X-Request-ID") ?? id;
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: "身份上下文已失效，请重新登录",
      403: "当前角色没有执行此操作的权限",
      409: "操作正在处理中，请刷新后再试",
      503: "Agent 服务暂时不可用，请稍后再试",
    };
    throw new ApiError(messages[response.status] ?? `请求失败（HTTP ${response.status}）`, response.status, responseRequestId);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function getCapabilities(): Promise<CapabilityCatalog> {
  return request<CapabilityCatalog>("/api/v1/agent/capabilities");
}

export function sendChat(conversationId: string, message: string): Promise<ChatResponse> {
  return request<ChatResponse>("/api/v1/agent/chat", {
    method: "POST",
    body: JSON.stringify({ conversation_id: conversationId, message, locale: "zh-CN" }),
  });
}

export function getConfirmation(id: string): Promise<Confirmation> {
  return request<Confirmation>(`/api/v1/agent/confirmations/${encodeURIComponent(id)}`);
}

export function decideConfirmation(
  id: string,
  decision: "APPROVE" | "REJECT",
  decisionRequestId: string = requestId(),
): Promise<Confirmation> {
  return request<Confirmation>(`/api/v1/agent/confirmations/${encodeURIComponent(id)}/decisions`, {
    method: "POST",
    body: JSON.stringify({
      decision,
      decision_request_id: decisionRequestId,
    }),
  });
}
