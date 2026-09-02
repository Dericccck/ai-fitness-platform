import type {
  CapabilityCatalog,
  ChatResponse,
  Confirmation,
  FitnessMemory,
  InAppNotification,
  KnowledgeReviewReport,
  KnowledgeJob,
  MemoryCandidateInbox,
  NotificationDeliveryAttempt,
  NotificationPreference,
  OperationsAuditPage,
  OperationsMetricCatalog,
  ReindexJob,
} from "./types";

const baseUrl = (import.meta.env.VITE_AGENT_API_BASE_URL ?? "").replace(/\/$/, "");
const configuredContext = import.meta.env.VITE_AGENT_CONTEXT?.trim() ?? "";
const configuredOrganizationId = import.meta.env.VITE_AGENT_ORGANIZATION_ID?.trim() ?? "";

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

function organizationId(): string {
  if (!configuredOrganizationId) {
    throw new ApiError("未配置本地机构 ID，请在 .env.local 中设置 VITE_AGENT_ORGANIZATION_ID", 0, null);
  }
  return configuredOrganizationId;
}

function withOrganization(path: string): string {
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}organization_id=${encodeURIComponent(organizationId())}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const id = requestId();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("X-Agent-Context", context());
  headers.set("X-Request-ID", id);
  headers.set("X-Trace-ID", id);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

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

export function getMemoryCandidateInbox(): Promise<MemoryCandidateInbox> {
  return request<MemoryCandidateInbox>(withOrganization("/api/v1/agent/memory-candidates/inbox"));
}

export function decideMemoryCandidate(
  id: string,
  decision: "APPROVE" | "REJECT",
  decisionRequestId: string,
): Promise<MemoryCandidateInbox["items"][number]["candidate"]> {
  return request<MemoryCandidateInbox["items"][number]["candidate"]>(
    `/api/v1/agent/memory-candidates/${encodeURIComponent(id)}/decisions`,
    { method: "POST", body: JSON.stringify({ decision, decision_request_id: decisionRequestId }) },
  );
}

export function getMemories(): Promise<FitnessMemory[]> {
  return request<FitnessMemory[]>(withOrganization("/api/v1/agent/memories"));
}

export function correctMemory(id: string, value: string, unit: string | null, expectedVersion: number, decisionRequestId: string): Promise<FitnessMemory> {
  return request<FitnessMemory>(`/api/v1/agent/memories/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify({ value, unit, expires_at: null, expected_version: expectedVersion, decision_request_id: decisionRequestId }),
  });
}

export function revokeMemory(id: string, expectedVersion: number, decisionRequestId: string): Promise<FitnessMemory> {
  return request<FitnessMemory>(`/api/v1/agent/memories/${encodeURIComponent(id)}/revocations`, {
    method: "POST",
    body: JSON.stringify({ expected_version: expectedVersion, decision_request_id: decisionRequestId }),
  });
}

export function getNotifications(): Promise<InAppNotification[]> {
  return request<InAppNotification[]>(withOrganization("/api/v1/agent/notifications"));
}

export function getNotificationPreference(): Promise<NotificationPreference> {
  return request<NotificationPreference>(withOrganization("/api/v1/agent/notifications/preferences"));
}

export function saveNotificationPreference(enabled: boolean): Promise<NotificationPreference> {
  return request<NotificationPreference>(withOrganization("/api/v1/agent/notifications/preferences"), {
    method: "PUT",
    body: JSON.stringify({ enabled, quiet_start: null, quiet_end: null, timezone: "Asia/Shanghai", minimum_interval_seconds: 0 }),
  });
}

export function markNotificationRead(id: string): Promise<InAppNotification> {
  return request<InAppNotification>(`/api/v1/agent/notifications/${encodeURIComponent(id)}/read`, { method: "POST" });
}

export function getOperationsMetricCatalog(): Promise<OperationsMetricCatalog> {
  return request<OperationsMetricCatalog>("/api/v1/admin/operations/metric-catalog");
}

export function getOperationsAudits(): Promise<OperationsAuditPage> {
  return request<OperationsAuditPage>("/api/v1/admin/operations/query-audits?limit=20&offset=0");
}

export function getKnowledgeJobs(): Promise<KnowledgeJob[]> {
  return request<KnowledgeJob[]>("/api/v1/admin/knowledge/jobs?limit=20");
}

export function getKnowledgeJob(id: string): Promise<KnowledgeJob> {
  return request<KnowledgeJob>(`/api/v1/admin/knowledge/jobs/${encodeURIComponent(id)}`);
}

export function getKnowledgeReviewReport(id: string): Promise<KnowledgeReviewReport> {
  return request<KnowledgeReviewReport>(`/api/v1/admin/knowledge/jobs/${encodeURIComponent(id)}/review-report`);
}

export function approveKnowledgeJob(id: string, comment: string): Promise<KnowledgeJob> {
  return request<KnowledgeJob>(`/api/v1/admin/knowledge/jobs/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: JSON.stringify(comment ? { comment } : {}),
  });
}

export function rejectKnowledgeJob(id: string, comment: string): Promise<KnowledgeJob> {
  return request<KnowledgeJob>(`/api/v1/admin/knowledge/jobs/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ comment }),
  });
}

export function retryKnowledgeJob(id: string): Promise<KnowledgeJob> {
  return request<KnowledgeJob>(`/api/v1/admin/knowledge/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" });
}

export function getReindexJobs(): Promise<ReindexJob[]> {
  return request<ReindexJob[]>("/api/v1/admin/knowledge/reindex/jobs?limit=20");
}

export function getNotificationDeliveryAttempts(): Promise<NotificationDeliveryAttempt[]> {
  return request<NotificationDeliveryAttempt[]>(withOrganization("/api/v1/admin/notifications/delivery-attempts?limit=20"));
}

export function uploadKnowledgeDocument(payload: FormData): Promise<KnowledgeJob> {
  return request<KnowledgeJob>("/api/v1/admin/knowledge/documents", { method: "POST", body: payload });
}
