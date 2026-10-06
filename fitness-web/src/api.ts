import type {
  CapabilityCatalog,
  ChatResponse,
  ChatRun,
  ChatStreamEvent,
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
  BusinessObjective,
  StrategyPolicy,
  DecisionCase,
  ActionRun,
  DecisionEffect,
  DecisionExperiment,
} from "./types";
import { SseParser } from "./sse";
import { AuthClient } from "./auth";

const baseUrl = (import.meta.env.VITE_AGENT_API_BASE_URL ?? "").replace(/\/$/, "");
const configuredContext = import.meta.env.DEV ? import.meta.env.VITE_AGENT_CONTEXT?.trim() ?? "" : "";
const configuredOrganizationId = import.meta.env.VITE_AGENT_ORGANIZATION_ID?.trim() ?? "";
const browserAuth = new AuthClient(baseUrl, fetch.bind(globalThis), () => {
  sessionStorage.removeItem("fitness.pending-chat");
  sessionStorage.removeItem("fitness-web-conversation-id");
  sessionStorage.removeItem("fitness-agent-context");
  sessionStorage.removeItem("fitness-agent-organization");
});
export async function getAuthState() {
  const config = await browserAuth.configuration();
  if (!config.enabled) return { config, authenticated: config.demo_identity_allowed };
  try { await browserAuth.load(); return { config, authenticated: true }; }
  catch (error) { if (error instanceof Error && "status" in error && error.status === 401) return { config, authenticated: false }; throw error; }
}
export const login = (username: string, password: string, organization: string) => browserAuth.login(username, password, organization);
export const logout = () => browserAuth.logout();

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
  const token = sessionStorage.getItem("fitness-agent-context") || configuredContext;
  if (!token) {
    throw new ApiError("请先接入身份：由可信认证端签发短期 AgentContext", 0, null);
  }
  return token;
}

function organizationId(): string {
  if (browserAuth.config?.enabled && browserAuth.session) return browserAuth.session.organization_id;
  const organization = sessionStorage.getItem("fitness-agent-organization") || configuredOrganizationId;
  if (!organization) {
    throw new ApiError("未配置本地机构 ID，请在 .env.local 中设置 VITE_AGENT_ORGANIZATION_ID", 0, null);
  }
  return organization;
}

// 演示/联调用运行时凭证，不能把用户签名身份烘焙进共享镜像。
// 生产登录集成应调用可信认证服务；浏览器输入的机构/角色绝不成为授权依据。
export function configureRuntimeIdentity(token: string, organization: string): void {
  if (!browserAuth.config?.demo_identity_allowed) throw new ApiError("当前环境不允许手工接入签名身份", 403, null);
  if (!token.trim() || !organization.trim()) throw new ApiError("签名身份和机构不能为空", 0, null);
  sessionStorage.setItem("fitness-agent-context", token.trim());
  sessionStorage.setItem("fitness-agent-organization", organization.trim());
  sessionStorage.removeItem("fitness.pending-chat");
  sessionStorage.removeItem("fitness-web-conversation-id");
}

function withOrganization(path: string): string {
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}organization_id=${encodeURIComponent(organizationId())}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const id = requestId();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const authHeaders = await browserAuth.headers(init.method ?? "GET", context);
  authHeaders.forEach((value, name) => headers.set(name, value));
  headers.set("X-Request-ID", id);
  headers.set("X-Trace-ID", id);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, { ...init, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError("无法连接 Agent 服务，请确认 8090 端口服务正在运行", 0, id);
  }

  const responseRequestId = response.headers.get("X-Request-ID") ?? id;
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: "身份上下文已失效，请重新登录",
      403: "当前角色没有执行此操作的权限",
      409: "操作正在处理中，请刷新后再试",
      429: "请求过于频繁，请稍后再试",
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

export function sendChat(conversationId: string, message: string, proactiveCandidateId?: string): Promise<ChatResponse> {
  return request<ChatResponse>("/api/v1/agent/chat", {
    method: "POST",
    body: JSON.stringify({
      conversation_id: conversationId,
      message,
      locale: "zh-CN",
      proactive_candidate_id: proactiveCandidateId,
    }),
  });
}

export function getChatRun(runId: string): Promise<ChatRun> {
  return request<ChatRun>(`/api/v1/agent/runs/${encodeURIComponent(runId)}`, { signal: AbortSignal.timeout(10_000) });
}

export async function recoverChatRun(runId: string): Promise<ChatResponse> {
  const deadline = Date.now() + 300_000;
  while (Date.now() < deadline) {
    const run = await getChatRun(runId);
    if (run.status === "COMPLETED" && run.response) return run.response;
    if (run.status !== "RUNNING") {
      throw new ApiError("本次处理未完成，请先核对业务结果后再发起新操作", 503, runId);
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new ApiError("处理仍未结束，请保留本次任务记录并稍后查询", 503, runId);
}

export async function sendChatStream(
  conversationId: string, message: string, runId: string,
  onEvent: (event: ChatStreamEvent) => void, proactiveCandidateId?: string,
): Promise<ChatResponse> {
  const id = requestId();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 300_000);
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let serverFailed = true;
  try {
    const headers = await browserAuth.headers("POST", context);
    headers.set("Accept", "text/event-stream");
    headers.set("Content-Type", "application/json");
    headers.set("X-Request-ID", id);
    headers.set("X-Trace-ID", id);
    serverFailed = false;
    const response = await fetch(`${baseUrl}/api/v1/agent/chat/stream`, {
      method: "POST", signal: controller.signal, credentials: "same-origin", headers,
      body: JSON.stringify({ conversation_id: conversationId, message, locale: "zh-CN",
        run_id: runId, proactive_candidate_id: proactiveCandidateId }),
    });
    if (!response.ok) {
      serverFailed = true;
      throw new ApiError(response.status === 429 ? "请求过于频繁，请稍后再试" : `对话请求失败（HTTP ${response.status}）`, response.status, response.headers.get("X-Request-ID") ?? id);
    }
    if (!response.body) throw new Error("缺少流式响应");
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    const parser = new SseParser();
    while (true) {
      const { value, done } = await reader.read();
      for (const frame of parser.push(decoder.decode(value, { stream: !done }))) {
        const event = JSON.parse(frame.data) as ChatStreamEvent;
        if (event.run_id !== runId || event.event !== frame.event) throw new Error("流式事件不匹配");
        onEvent(event);
        if (event.event === "run.completed" && event.data.response) return event.data.response;
        if (event.event === "run.failed") {
          serverFailed = true;
          // 终态通知可能先于客户端收到，或持久化 ACK 不确定；以数据库终态为准。
          return await recoverChatRun(runId);
        }
        if (event.event === "stream.reset") return await recoverChatRun(runId);
      }
      if (done) break;
    }
  } catch (error: unknown) {
    if (serverFailed) throw error;
    // 网络故障只查询已提交任务，绝不重新 POST 对话或重复业务写入。
    return await recoverChatRun(runId);
  } finally {
    clearTimeout(timer);
    if (reader) {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
  }
  return await recoverChatRun(runId);
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

export function respondToProactiveNotification(
  candidateId: string,
  responseType: "OPENED" | "ACCEPTED" | "DECLINED" | "SNOOZED" | "MUTED" | "HELPFUL" | "NOT_HELPFUL",
  notificationId: string,
  responseValue?: string,
): Promise<void> {
  return request<void>(`/api/v1/agent/notifications/proactive/${encodeURIComponent(candidateId)}/responses`, {
    method: "POST",
    body: JSON.stringify({
      response_type: responseType,
      response_value: responseValue,
      notification_id: notificationId,
    }),
  });
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

export function getBusinessObjectives(): Promise<BusinessObjective[]> {
  return request<BusinessObjective[]>(withOrganization("/api/v1/admin/decisions/objectives"));
}

export function createBusinessObjective(payload: {
  objective_key: string;
  name: string;
  metric: string;
  direction: "INCREASE" | "DECREASE" | "TARGET";
  target_value: number | null;
}): Promise<BusinessObjective> {
  return request<BusinessObjective>("/api/v1/admin/decisions/objectives", {
    method: "POST",
    body: JSON.stringify({
      ...payload,
      organization_id: organizationId(),
      guardrail_metrics: [],
    }),
  });
}

export function getStrategyPolicies(): Promise<StrategyPolicy[]> {
  return request<StrategyPolicy[]>(withOrganization("/api/v1/admin/decisions/policies"));
}

export function createStrategyPolicy(
  objectiveId: string,
  payload: {
    strategy_key: string;
    mode: StrategyPolicy["mode"];
    risk_level: StrategyPolicy["risk_level"];
    allowed_actions: string[];
  },
): Promise<StrategyPolicy> {
  return request<StrategyPolicy>(`/api/v1/admin/decisions/objectives/${encodeURIComponent(objectiveId)}/policies`, {
    method: "POST",
    body: JSON.stringify({
      ...payload,
      organization_id: organizationId(),
      config: { history_periods: 28, minimum_samples: 7, relative_threshold: 0.15, z_threshold: 3.5, minimum_confidence: 0.68 },
      budget_limit: 0,
      max_affected_subjects: 1,
      valid_to: null,
    }),
  });
}

export function getDecisionCases(): Promise<DecisionCase[]> {
  return request<DecisionCase[]>(withOrganization("/api/v1/admin/decisions/cases?limit=100"));
}

export function getDecisionCase(id: string): Promise<DecisionCase> {
  return request<DecisionCase>(withOrganization(`/api/v1/admin/decisions/cases/${encodeURIComponent(id)}`));
}

export function decideBusinessCase(
  caseId: string,
  optionId: string,
  decision: "APPROVE" | "REJECT",
  decisionRequestId: string,
): Promise<{ approval_id: string; action_run_id: string | null }> {
  return request(withOrganization(`/api/v1/admin/decisions/cases/${encodeURIComponent(caseId)}/decisions`), {
    method: "POST",
    body: JSON.stringify({ option_id: optionId, decision, decision_request_id: decisionRequestId }),
  });
}

export function getDecisionActions(): Promise<ActionRun[]> {
  return request<ActionRun[]>(withOrganization("/api/v1/admin/decisions/actions?limit=100"));
}

export function cancelDecisionAction(actionId: string): Promise<void> {
  return request<void>(withOrganization(`/api/v1/admin/decisions/actions/${encodeURIComponent(actionId)}/cancel`), { method: "POST" });
}

export function retryDecisionAction(actionId: string): Promise<void> {
  return request<void>(withOrganization(`/api/v1/admin/decisions/actions/${encodeURIComponent(actionId)}/retry`), { method: "POST" });
}

export function getDecisionEffects(caseId: string): Promise<DecisionEffect[]> {
  return request<DecisionEffect[]>(withOrganization(`/api/v1/admin/decisions/cases/${encodeURIComponent(caseId)}/effects`));
}

export function closeDecisionCase(caseId: string): Promise<void> {
  return request<void>(withOrganization(`/api/v1/admin/decisions/cases/${encodeURIComponent(caseId)}/close`), { method: "POST" });
}

export function getDecisionExperiments(): Promise<DecisionExperiment[]> {
  return request<DecisionExperiment[]>(withOrganization("/api/v1/admin/decisions/experiments?limit=100"));
}

export function createDecisionExperiment(payload: {
  policy_id: string;
  name: string;
  treatment_percent: number;
  primary_metric: string;
  observation_days: number;
}): Promise<DecisionExperiment> {
  return request<DecisionExperiment>("/api/v1/admin/decisions/experiments", {
    method: "POST",
    body: JSON.stringify({
      ...payload,
      organization_id: organizationId(),
      guardrail_metrics: [],
    }),
  });
}

export function updateDecisionExperimentStatus(
  experimentId: string,
  status: "RUNNING" | "STOPPED" | "COMPLETED",
): Promise<DecisionExperiment> {
  return request<DecisionExperiment>(withOrganization(`/api/v1/admin/decisions/experiments/${encodeURIComponent(experimentId)}/status`), {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}
