import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  approveKnowledgeJob,
  cancelDecisionAction,
  closeDecisionCase,
  createBusinessObjective,
  createDecisionExperiment,
  createStrategyPolicy,
  correctMemory,
  configureRuntimeIdentity,
  decideConfirmation,
  decideMemoryCandidate,
  decideBusinessCase,
  getBusinessObjectives,
  getCapabilities,
  getAuthState,
  login,
  logout,
  getConfirmation,
  getDecisionActions,
  getDecisionCase,
  getDecisionCases,
  getDecisionEffects,
  getDecisionExperiments,
  getKnowledgeJob,
  getKnowledgeReviewReport,
  getKnowledgeJobs,
  getMemories,
  getMemoryCandidateInbox,
  getNotifications,
  getOperationsAudits,
  getOperationsMetricCatalog,
  getNotificationDeliveryAttempts,
  getReindexJobs,
  getNotificationPreference,
  getStrategyPolicies,
  markNotificationRead,
  respondToProactiveNotification,
  rejectKnowledgeJob,
  revokeMemory,
  retryKnowledgeJob,
  retryDecisionAction,
  saveNotificationPreference,
  sendChatStream,
  recoverChatRun,
  uploadKnowledgeDocument,
  updateDecisionExperimentStatus,
} from "./api";
import type {
  Capability,
  CapabilityCatalog,
  ChatMessage,
  ChatResponse,
  Confirmation,
  KnowledgeReviewReport,
  FitnessMemory,
  InAppNotification,
  KnowledgeJob,
  MemoryCandidateInbox,
  NotificationPreference,
  NotificationDeliveryAttempt,
  OperationsAuditPage,
  OperationsMetricCatalog,
  ReindexJob,
  BusinessObjective,
  StrategyPolicy,
  DecisionCase,
  DecisionOption,
  ActionRun,
  DecisionEffect,
  DecisionExperiment,
} from "./types";
import type { FormEvent } from "react";

const roleNames: Record<string, string> = {
  ORGANIZATION_ADMIN: "组织管理员",
  COACH: "教练",
  STUDENT: "学员",
  SYSTEM_ADMIN: "系统管理员",
};

const domainNames: Record<string, string> = {
  BUSINESS: "业务资料",
  BOOKING: "预约",
  TRAINING: "训练计划",
  MEMORY: "个人偏好",
  OPERATIONS: "经营分析",
  CUSTOMER_SERVICE: "客服",
  OTHER: "其他能力",
};

const domainIcons: Record<string, string> = {
  BUSINESS: "◇",
  BOOKING: "◷",
  TRAINING: "✦",
  MEMORY: "⌁",
  OPERATIONS: "▥",
  CUSTOMER_SERVICE: "◌",
};

const domainQuickActions: Record<string, { title: string; description: string; prompt: string }> = {
  BUSINESS: { title: "业务资料查询", description: "查询课程、合同和课时等当前业务事实。", prompt: "查询我有权限查看的课程、合同和剩余课时" },
  BOOKING: { title: "预约工作台", description: "查看预约、可预约时间和课时约束。", prompt: "查询我最近的课程预约和可用课时" },
  TRAINING: { title: "训练计划工作台", description: "查看已发布计划，或发起训练计划草案。", prompt: "查看我当前已发布的训练计划" },
  MEMORY: { title: "个人偏好", description: "查看已确认的训练偏好和助手记忆。", prompt: "查看我的已确认训练偏好" },
  OPERATIONS: { title: "经营分析", description: "查询当前身份可见的固定经营指标。", prompt: "查看本机构本月经营指标" },
  CUSTOMER_SERVICE: { title: "客服工单", description: "查看本人可见的客服问题和处理状态。", prompt: "查询我最近的客服工单" },
};

function proactivePrompt(ctaType: string | null): string {
  const prompts: Record<string, string> = {
    OPEN_TRAINING_PLAN: "请结合这条主动建议，帮我查看当前训练计划",
    ADJUST_TRAINING_PLAN: "请结合这条主动建议，帮我制定训练计划调整方案",
    RESUME_TRAINING: "请结合这条主动建议，帮我安排恢复训练的下一步",
    BOOK_APPOINTMENT: "请结合这条主动建议，帮我查看可预约时间",
    RESCHEDULE_APPOINTMENT: "请结合这条主动建议，帮我重新安排预约",
    CONTACT_COACH: "请结合这条主动建议，帮我联系教练",
    OPEN_CUSTOMER_SERVICE: "请结合这条主动建议，帮我继续处理客服问题",
    REVIEW_STUDENTS: "请结合这条主动建议，帮我查看需要关注的学员",
    REVIEW_OPERATIONS: "请结合这条主动建议，帮我查看运营待办",
  };
  return (ctaType && prompts[ctaType]) || "请结合这条主动建议，告诉我下一步可以怎么做";
}

function stableId(prefix: string): string {
  return `${prefix}-${typeof crypto.randomUUID === "function" ? crypto.randomUUID() : Date.now()}`;
}

function initialConversationId(): string {
  const key = "fitness-web-conversation-id";
  const existing = sessionStorage.getItem(key);
  if (existing) return existing;
  const created = stableId("web");
  sessionStorage.setItem(key, created);
  return created;
}

function displayStatus(status: string): string {
  const values: Record<string, string> = {
    PENDING: "待确认",
    APPROVED: "已批准",
    REJECTED: "已拒绝",
    EXPIRED: "已过期",
    CANCELLED: "已撤销",
    NOT_STARTED: "未执行",
    RUNNING: "执行中",
    SUCCEEDED: "执行成功",
    FAILED_RETRYABLE: "可重试失败",
    FAILED_FINAL: "最终失败",
  };
  return values[status] ?? status;
}

function formatError(error: unknown): string {
  if (error instanceof ApiError && error.requestId) return `${error.message}（请求 ID：${error.requestId}）`;
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}

function groupCapabilities(items: Capability[]): [string, Capability[]][] {
  const groups = new Map<string, Capability[]>();
  for (const item of items) groups.set(item.domain, [...(groups.get(item.domain) ?? []), item]);
  return [...groups.entries()];
}

function App() {
  const [catalog, setCatalog] = useState<CapabilityCatalog | null>(null);
  const [activeView, setActiveView] = useState<"chat" | "workspaces" | "decisions" | "data" | "capabilities">("chat");
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: stableId("assistant"),
      role: "assistant",
      content: "你好，我是健身工作台。你可以询问训练动作、课程、合同、预约或已发布训练计划。涉及写入的操作会先展示确认卡片。",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [streamStage, setStreamStage] = useState("");
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [error, setError] = useState("");
  const [identityOpen, setIdentityOpen] = useState(false);
  const [identityToken, setIdentityToken] = useState("");
  const [identityOrganization, setIdentityOrganization] = useState("");
  const [authEnabled, setAuthEnabled] = useState(false);
  const [demoIdentityAllowed, setDemoIdentityAllowed] = useState(false);
  const [authUsername, setAuthUsername] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [proactiveCandidateId, setProactiveCandidateId] = useState<string | undefined>();
  const conversationId = useMemo(initialConversationId, []);

  useEffect(() => {
    getAuthState().then(({ config, authenticated }) => {
      setAuthEnabled(config.enabled);
      setDemoIdentityAllowed(config.demo_identity_allowed);
      if (!authenticated) { setIdentityOpen(true); return null; }
      return getCapabilities();
    })
      .then(setCatalog)
      .catch((reason: unknown) => setError(formatError(reason)))
      .finally(() => setLoadingCatalog(false));
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || loading) return;
    setError("");
    setInput("");
    const assistantId = stableId("assistant");
    const runId = crypto.randomUUID();
    sessionStorage.setItem("fitness.pending-chat", JSON.stringify({ runId, conversationId }));
    setMessages((current) => [...current,
      { id: stableId("user"), role: "user", content: message },
      { id: assistantId, role: "assistant", content: "正在准备回复…" }]);
    setLoading(true);
    setStreamStage("正在处理");
    let streamedText = "";
    try {
      const result = await sendChatStream(conversationId, message, runId, (event) => {
        if (event.event === "answer.delta" && event.data.text) {
          streamedText += event.data.text;
          setMessages((current) => current.map((item) => item.id === assistantId ? { ...item, content: streamedText } : item));
        }
        if (event.event === "stage.changed") {
          const labels: Record<string, string> = { ROUTING: "正在理解问题", PREPARING_CONTEXT: "正在查找相关资料", QUERYING_BUSINESS: "正在查询业务信息", GENERATING: "正在组织回复" };
          setStreamStage(labels[event.data.stage ?? ""] ?? "正在处理");
        }
      }, proactiveCandidateId);
      sessionStorage.removeItem("fitness.pending-chat");
      setProactiveCandidateId(undefined);
      setMessages((current) => current.map((item) => item.id === assistantId ? {
        ...item, content: result.answer, route: result.route,
        confirmation: result.confirmation_id ? result : undefined,
      } : item));
    } catch (reason: unknown) {
      setMessages((current) => current.map((item) => item.id === assistantId ? {
        ...item, content: "本次回复未完成，请核对操作结果后再继续。",
      } : item));
      setError(formatError(reason));
    } finally {
      setLoading(false);
      setStreamStage("");
    }
  }

  useEffect(() => {
    if (!catalog) return;
    const stored = sessionStorage.getItem("fitness.pending-chat");
    if (!stored) return;
    let pending: { runId: string; conversationId: string };
    try { pending = JSON.parse(stored); } catch { sessionStorage.removeItem("fitness.pending-chat"); return; }
    if (pending.conversationId !== conversationId) return;
    let cancelled = false;
    setLoading(true);
    setStreamStage("正在恢复上次回复");
    recoverChatRun(pending.runId).then((result) => {
      if (cancelled) return;
      sessionStorage.removeItem("fitness.pending-chat");
      setMessages((current) => [...current, { id: stableId("assistant"), role: "assistant",
        content: result.answer, route: result.route, confirmation: result.confirmation_id ? result : undefined }]);
    }).catch((reason: unknown) => { if (!cancelled) setError(formatError(reason)); })
      .finally(() => { if (!cancelled) { setLoading(false); setStreamStage(""); } });
    return () => { cancelled = true; };
  }, [catalog, conversationId]);

  const roles = catalog?.roles ?? [];
  const groups = catalog ? groupCapabilities(catalog.items) : [];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">F</div>
          <div><strong>FITNESS AI</strong><span>健身智能工作台</span></div>
        </div>
        <div className="workspace-label">工作区</div>
        <button className={`nav-item ${activeView === "chat" ? "active" : ""}`} onClick={() => setActiveView("chat")}>
          <span>✧</span> Agent 对话
        </button>
        <button className={`nav-item ${activeView === "workspaces" ? "active" : ""}`} onClick={() => setActiveView("workspaces")}>
          <span>▦</span> 业务工作台
        </button>
        <button className={`nav-item ${activeView === "data" ? "active" : ""}`} onClick={() => setActiveView("data")}>
          <span>◈</span> 数据中心
        </button>
        <button className={`nav-item ${activeView === "decisions" ? "active" : ""}`} onClick={() => setActiveView("decisions")}>
          <span>◎</span> 经营决策
        </button>
        <button className={`nav-item ${activeView === "capabilities" ? "active" : ""}`} onClick={() => setActiveView("capabilities")}>
          <span>◫</span> 我的能力
        </button>
        <div className="sidebar-spacer" />
        <div className="security-note"><span className="status-dot" /> 权限由服务端实时校验</div>
        <div className="user-card">
          <div className="avatar">{roles[0]?.slice(0, 1) ?? "?"}</div>
          <div><strong>{roles[0] ? roleNames[roles[0]] ?? roles[0] : "未识别角色"}</strong><span>{loadingCatalog ? "正在读取能力…" : catalog ? "服务端身份已验证" : "等待登录与身份验证"}</span></div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div><span className="eyebrow">AI FITNESS PLATFORM</span><h1>{activeView === "chat" ? "智能健身助手" : activeView === "workspaces" ? "业务工作台" : activeView === "decisions" ? "经营决策中心" : activeView === "data" ? "数据中心" : "能力目录"}</h1></div>
          <div className="topbar-meta"><span className="live-pill"><i /> {catalog ? "Agent 已连接" : "等待认证连接"}</span><span className="role-pill">{roles.map((role) => roleNames[role] ?? role).join(" / ") || "等待身份"}</span>{(authEnabled || demoIdentityAllowed) && <button disabled={loading || authBusy} onClick={() => setIdentityOpen(!identityOpen)}>{authEnabled ? "登录 / 切换账号" : "接入身份"}</button>}{authEnabled && catalog && <button disabled={loading || authBusy} onClick={async () => { setAuthBusy(true); try { await logout(); window.location.reload(); } catch (reason) { setError(formatError(reason)); } finally { setAuthBusy(false); } }}>退出登录</button>}</div>
        </header>

        {identityOpen && authEnabled && <form className="identity-form" onSubmit={async (event) => {
          event.preventDefault(); setAuthBusy(true); setError("");
          try { await login(authUsername.trim(), authPassword, identityOrganization.trim()); setAuthPassword(""); window.location.reload(); }
          catch (reason) { setAuthPassword(""); setError(formatError(reason)); }
          finally { setAuthBusy(false); }
        }}>
          <p>使用可信认证服务登录。身份权限由服务端验证，浏览器不保存签名 Token 或刷新凭证。</p>
          <label>账号<input autoComplete="username" value={authUsername} onChange={(event) => setAuthUsername(event.target.value)} required /></label>
          <label>密码<input type="password" autoComplete="current-password" value={authPassword} onChange={(event) => setAuthPassword(event.target.value)} required /></label>
          <label>当前机构<input value={identityOrganization} onChange={(event) => setIdentityOrganization(event.target.value)} required /></label>
          <button type="submit" disabled={authBusy}>{authBusy ? "正在登录…" : "登录并连接"}</button>
        </form>}
        {identityOpen && demoIdentityAllowed && <form className="identity-form" onSubmit={(event) => {
          event.preventDefault();
          try { configureRuntimeIdentity(identityToken, identityOrganization); window.location.reload(); }
          catch (reason) { setError(formatError(reason)); }
        }}>
          <p>演示／联调入口：粘贴可信认证端签发的短期 AgentContext。生产环境应接入登录流程，不能在前端自行签发身份。</p>
          <label>签名上下文<input type="password" autoComplete="off" value={identityToken} onChange={(event) => setIdentityToken(event.target.value)} required /></label>
          <label>当前机构<input value={identityOrganization} onChange={(event) => setIdentityOrganization(event.target.value)} required /></label>
          <button type="submit">验证并连接</button>
        </form>}

        {error && <div className="error-banner"><strong>请求未完成</strong><span>{error}</span><button onClick={() => setError("")}>×</button></div>}

        {activeView === "chat" ? (
          <section className="chat-layout">
            <div className="chat-panel">
              <div className="panel-heading"><div><span className="panel-kicker">CONVERSATION</span><h2>和你的健身助手聊聊</h2></div><span className="conversation-label">会话已持久化</span></div>
              <div className="message-list">
                {messages.map((message) => <MessageBubble key={message.id} message={message} onDecision={setError} />)}
                {loading && <div className="message-row assistant"><div className="assistant-avatar">F</div><div className="message-bubble" role="status">{streamStage || "正在处理"}</div></div>}
              </div>
              <form className="composer" onSubmit={submit}><textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder="例如：我今天适合做哪些热身动作？" rows={2} disabled={loading} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(event); } }} /><button type="submit" disabled={loading || !input.trim()}>{loading ? "处理中" : "发送"}<span>↗</span></button><small>Enter 发送 · Shift + Enter 换行</small></form>
            </div>
            <aside className="context-panel"><div className="panel-heading compact"><div><span className="panel-kicker">ACCESS</span><h3>当前可用能力</h3></div></div><div className="capability-list">{groups.slice(0, 5).map(([domain, items]) => <div className="capability-group" key={domain}><div className="group-title"><span>{domainIcons[domain] ?? "◇"}</span>{domainNames[domain] ?? domain}<em>{items.length}</em></div><div className="group-items">{items.slice(0, 3).map((item) => <span key={item.id}>{item.display_name}</span>)}</div></div>)}{!catalog && <div className="empty-state">配置 AgentContext 后加载能力目录</div>}</div><button className="text-button" onClick={() => setActiveView("capabilities")}>查看全部能力 <span>→</span></button></aside>
          </section>
        ) : activeView === "workspaces" ? <DomainWorkspacesView catalog={catalog} onUsePrompt={(prompt) => { setInput(prompt); setActiveView("chat"); }} /> : activeView === "decisions" ? <DecisionCenterView catalog={catalog} /> : activeView === "data" ? <DataCenterView catalog={catalog} onOpenAgent={(notification) => { setInput(proactivePrompt(notification.cta_type)); setProactiveCandidateId(notification.proactive_candidate_id ?? undefined); setActiveView("chat"); }} /> : <CapabilitiesView catalog={catalog} />}
      </main>
    </div>
  );
}

function MessageBubble({ message, onDecision }: { message: ChatMessage; onDecision: (message: string) => void }) {
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [acting, setActing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const decisionRequestIds = useMemo(() => ({
    APPROVE: stableId("approve"),
    REJECT: stableId("reject"),
  }), []);

  async function refreshConfirmation() {
    if (!message.confirmation?.confirmation_id) return;
    setRefreshing(true);
    try {
      setConfirmation(await getConfirmation(message.confirmation.confirmation_id));
    } catch (reason: unknown) {
      onDecision(formatError(reason));
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    if (message.confirmation?.confirmation_id) void refreshConfirmation();
  }, [message.confirmation?.confirmation_id]);

  useEffect(() => {
    if (!confirmation || confirmation.authorization_status !== "APPROVED") return;
    if (["SUCCEEDED", "FAILED_FINAL"].includes(confirmation.execution_status)) return;
    const timer = window.setInterval(() => void refreshConfirmation(), 2000);
    return () => window.clearInterval(timer);
  }, [confirmation?.authorization_status, confirmation?.execution_status]);

  async function decide(decision: "APPROVE" | "REJECT") {
    if (!message.confirmation?.confirmation_id || acting) return;
    setActing(true);
    try {
      const updated = await decideConfirmation(
        message.confirmation.confirmation_id,
        decision,
        decisionRequestIds[decision],
      );
      setConfirmation(updated);
      onDecision("");
    } catch (reason: unknown) {
      onDecision(formatError(reason));
    } finally {
      setActing(false);
    }
  }

  return <div className={`message-row ${message.role}`}><div className={message.role === "assistant" ? "assistant-avatar" : "user-avatar"}>{message.role === "assistant" ? "F" : "我"}</div><div className="message-content">{message.route && <span className="route-tag">{domainNames[message.route] ?? message.route}</span>}<div className="message-bubble">{message.content}</div>{message.confirmation && <ConfirmationCard response={message.confirmation} confirmation={confirmation} acting={acting} refreshing={refreshing} onRefresh={() => void refreshConfirmation()} onDecision={decide} />}</div></div>;
}

function ConfirmationCard({ response, confirmation, acting, refreshing, onRefresh, onDecision }: { response: ChatResponse; confirmation: Confirmation | null; acting: boolean; refreshing: boolean; onRefresh: () => void; onDecision: (decision: "APPROVE" | "REJECT") => void }) {
  const authorization = confirmation?.authorization_status ?? "PENDING";
  const execution = confirmation?.execution_status ?? "NOT_STARTED";
  const canDecide = authorization === "PENDING" && !acting;
  return <div className="confirmation-card"><div className="confirmation-head"><span className="shield">✓</span><div><strong>需要你的确认</strong><small>这是一个可能修改业务数据的操作</small></div><span className="risk-tag">受控写入</span></div><div className="confirmation-body"><div><span>动作</span><strong>{response.confirmation_summary?.action ?? confirmation?.action ?? "业务操作"}</strong></div><div><span>资源</span><strong>{response.confirmation_summary?.resource_type ?? confirmation?.resource_type ?? "健身业务"}</strong></div>{response.confirmation_expires_at && <div><span>有效期至</span><strong>{new Date(response.confirmation_expires_at).toLocaleString("zh-CN")}</strong></div>}</div><div className="confirmation-status"><span>授权：<b>{displayStatus(authorization)}</b></span><span>执行：<b>{displayStatus(execution)}</b></span></div>{canDecide ? <div className="confirmation-actions"><button className="reject-button" onClick={() => onDecision("REJECT")}>拒绝</button><button className="approve-button" onClick={() => onDecision("APPROVE")}>{acting ? "处理中…" : "确认执行"}</button></div> : <div className="confirmation-locked">{acting ? "正在处理…" : `当前状态：${displayStatus(authorization)} / ${displayStatus(execution)}`}</div>}<button className="refresh-button" onClick={onRefresh} disabled={refreshing}>{refreshing ? "刷新中…" : "刷新确认状态"}</button></div>;
}

function CapabilitiesView({ catalog }: { catalog: CapabilityCatalog | null }) {
  if (!catalog) return <div className="empty-page"><span>◇</span><h2>暂时无法加载能力目录</h2><p>请确认 AgentContext 已配置，并检查 Agent 服务是否在线。</p></div>;
  return <section className="catalog-page"><div className="catalog-intro"><div><span className="panel-kicker">SERVER-DRIVEN ACCESS</span><h2>你可以使用的能力</h2><p>以下目录由后端根据签名角色实时生成。页面只负责展示，真正的权限仍由 Agent、Tool Registry 和 Gateway 再次校验。</p></div><span className="version-badge">{catalog.catalog_version.slice(0, 20)}…</span></div><div className="catalog-grid">{groupCapabilities(catalog.items).map(([domain, items]) => <div className="catalog-card" key={domain}><div className="catalog-card-title"><span>{domainIcons[domain] ?? "◇"}</span><div><h3>{domainNames[domain] ?? domain}</h3><small>{items.length} 项能力</small></div></div>{items.map((item) => <div className="catalog-item" key={item.id}><div><strong>{item.display_name}</strong><p>{item.description}</p></div><div className="item-flags"><span className={item.read_only ? "read-tag" : "write-tag"}>{item.read_only ? "只读" : "写入"}</span>{item.requires_confirmation && <span className="confirm-tag">需确认</span>}</div></div>)}</div>)}</div></section>;
}

function DomainWorkspacesView({ catalog, onUsePrompt }: { catalog: CapabilityCatalog | null; onUsePrompt: (prompt: string) => void }) {
  if (!catalog) return <div className="empty-page"><span>▦</span><h2>暂时无法加载业务工作台</h2><p>请先配置 AgentContext，页面会根据服务端权限显示入口。</p></div>;
  const availableDomains = groupCapabilities(catalog.items).filter(([domain]) => domainQuickActions[domain]);
  return <section className="workspace-page"><div className="catalog-intro"><div><span className="panel-kicker">DOMAIN WORKSPACES</span><h2>从业务场景开始</h2><p>每个入口都会回到同一个 Agent 会话，由服务端重新校验身份、组织范围和工具权限。写操作仍然需要确认。</p></div><span className="version-badge">{availableDomains.length} 个可用领域</span></div><div className="workspace-grid">{availableDomains.map(([domain, items]) => { const action = domainQuickActions[domain]; return <article className="workspace-card" key={domain}><div className="workspace-card-head"><span>{domainIcons[domain] ?? "◇"}</span><div><h3>{action.title}</h3><small>{items.length} 项能力可用</small></div></div><p>{action.description}</p><div className="workspace-actions"><button className="workspace-primary" onClick={() => onUsePrompt(action.prompt)}>进入查询 <span>→</span></button><span>{items.some((item) => !item.read_only) ? "含受控写入" : "只读查询"}</span></div></article>; })}</div></section>;
}

type DecisionCenterState = {
  objectives: BusinessObjective[];
  policies: StrategyPolicy[];
  cases: DecisionCase[];
  actions: ActionRun[];
  experiments: DecisionExperiment[];
};

const emptyDecisionCenter: DecisionCenterState = {
  objectives: [],
  policies: [],
  cases: [],
  actions: [],
  experiments: [],
};

function DecisionCenterView({ catalog }: { catalog: CapabilityCatalog | null }) {
  const [data, setData] = useState<DecisionCenterState>(emptyDecisionCenter);
  const [selected, setSelected] = useState<DecisionCase | null>(null);
  const [effects, setEffects] = useState<DecisionEffect[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [objectiveDraft, setObjectiveDraft] = useState({ objectiveKey: "", name: "", metric: "", direction: "INCREASE" as "INCREASE" | "DECREASE" | "TARGET", targetValue: "" });
  const [policyDraft, setPolicyDraft] = useState({ objectiveId: "", strategyKey: "", mode: "SHADOW" as StrategyPolicy["mode"], riskLevel: "L2" as StrategyPolicy["risk_level"], action: "CREATE_OPERATIONS_TASK" });
  const [experimentDraft, setExperimentDraft] = useState({ policyId: "", name: "", treatmentPercent: "50", observationDays: "7" });
  const decisionIds = useRef(new Map<string, string>());
  const isAdmin = Boolean(catalog?.roles.some((role) => ["SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "ADMIN", "SUPER_ADMIN"].includes(role)));

  useEffect(() => {
    if (!isAdmin) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    Promise.all([getBusinessObjectives(), getStrategyPolicies(), getDecisionCases(), getDecisionActions(), getDecisionExperiments()])
      .then(([objectives, policies, cases, actions, experiments]) => {
        setData({ objectives, policies, cases, actions, experiments });
        if (selected) {
          const refreshed = cases.find((item) => item.id === selected.id);
          if (!refreshed) setSelected(null);
        }
      })
      .catch((reason: unknown) => setError(formatError(reason)))
      .finally(() => setLoading(false));
  }, [isAdmin, refreshKey]);

  async function openCase(item: DecisionCase) {
    setBusy(true);
    setError("");
    try {
      const [detail, caseEffects] = await Promise.all([
        getDecisionCase(item.id),
        getDecisionEffects(item.id),
      ]);
      setSelected(detail);
      setEffects(caseEffects);
    } catch (reason: unknown) {
      setError(formatError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function refreshSelected(caseId: string) {
    const [detail, caseEffects] = await Promise.all([
      getDecisionCase(caseId),
      getDecisionEffects(caseId),
    ]);
    setSelected(detail);
    setEffects(caseEffects);
    setRefreshKey((value) => value + 1);
  }

  async function decide(item: DecisionCase, option: DecisionOption, decision: "APPROVE" | "REJECT") {
    if (busy) return;
    const key = `${item.id}:${option.id}:${decision}`;
    const decisionId = decisionIds.current.get(key) ?? stableId(`business-${decision.toLowerCase()}`);
    decisionIds.current.set(key, decisionId);
    setBusy(true);
    setError("");
    try {
      await decideBusinessCase(item.id, option.id, decision, decisionId);
      await refreshSelected(item.id);
    } catch (reason: unknown) {
      setError(formatError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function updateAction(action: ActionRun, operation: "cancel" | "retry") {
    if (busy || !selected) return;
    setBusy(true);
    setError("");
    try {
      if (operation === "retry") await retryDecisionAction(action.id);
      else await cancelDecisionAction(action.id);
      await refreshSelected(selected.id);
    } catch (reason: unknown) {
      setError(formatError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function closeCase() {
    if (busy || !selected) return;
    setBusy(true);
    setError("");
    try {
      await closeDecisionCase(selected.id);
      await refreshSelected(selected.id);
    } catch (reason: unknown) {
      setError(formatError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitObjective(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await createBusinessObjective({
        objective_key: objectiveDraft.objectiveKey.trim().toUpperCase(),
        name: objectiveDraft.name.trim(),
        metric: objectiveDraft.metric.trim().toUpperCase(),
        direction: objectiveDraft.direction,
        target_value: objectiveDraft.direction === "TARGET" ? Number(objectiveDraft.targetValue) : null,
      });
      setObjectiveDraft({ objectiveKey: "", name: "", metric: "", direction: "INCREASE", targetValue: "" });
      setPolicyDraft((current) => ({ ...current, objectiveId: created.id }));
      setRefreshKey((value) => value + 1);
    } catch (reason: unknown) {
      setError(formatError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitPolicy(event: FormEvent) {
    event.preventDefault();
    if (busy || !policyDraft.objectiveId) return;
    setBusy(true);
    setError("");
    try {
      await createStrategyPolicy(policyDraft.objectiveId, {
        strategy_key: policyDraft.strategyKey.trim().toUpperCase(),
        mode: policyDraft.mode,
        risk_level: policyDraft.riskLevel,
        allowed_actions: [policyDraft.action],
      });
      setPolicyDraft((current) => ({ ...current, strategyKey: "", mode: "SHADOW" }));
      setRefreshKey((value) => value + 1);
    } catch (reason: unknown) {
      setError(formatError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitExperiment(event: FormEvent) {
    event.preventDefault();
    if (busy || !experimentDraft.policyId) return;
    const policy = data.policies.find((item) => item.id === experimentDraft.policyId);
    if (!policy) return;
    setBusy(true);
    setError("");
    try {
      await createDecisionExperiment({
        policy_id: policy.id,
        name: experimentDraft.name.trim(),
        treatment_percent: Number(experimentDraft.treatmentPercent),
        primary_metric: policy.objective_metric,
        observation_days: Number(experimentDraft.observationDays),
      });
      setExperimentDraft({ policyId: "", name: "", treatmentPercent: "50", observationDays: "7" });
      setRefreshKey((value) => value + 1);
    } catch (reason: unknown) {
      setError(formatError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function changeExperimentStatus(experiment: DecisionExperiment, status: "RUNNING" | "STOPPED" | "COMPLETED") {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await updateDecisionExperimentStatus(experiment.id, status);
      setRefreshKey((value) => value + 1);
    } catch (reason: unknown) {
      setError(formatError(reason));
    } finally {
      setBusy(false);
    }
  }

  if (!isAdmin) return <div className="empty-page"><span>◎</span><h2>当前角色没有经营决策权限</h2><p>经营目标、策略、审批和执行结果只向受授权管理员开放。</p></div>;
  const waiting = data.cases.filter((item) => item.status === "WAITING_APPROVAL").length;
  const running = data.actions.filter((item) => ["QUEUED", "RUNNING", "RETRYABLE_FAILED"].includes(item.status)).length;
  return <section className="decision-page">
    <div className="catalog-intro"><div><span className="panel-kicker">CONTROLLED BUSINESS DECISIONS</span><h2>从经营异常到效果验证</h2><p>所有方案都绑定指标证据、策略版本和风险等级；影子模式不执行动作，中高风险动作必须经过明确审批。</p></div><button className="outline-button" disabled={loading} onClick={() => setRefreshKey((value) => value + 1)}>{loading ? "加载中…" : "刷新"}</button></div>
    {error && <div className="partial-warning"><strong>读取未完成</strong><span>{error}</span></div>}
    <div className="decision-setup-grid">
      <form className="decision-setup" onSubmit={(event) => void submitObjective(event)}>
        <div><span className="panel-kicker">OBJECTIVE</span><h3>新建经营目标</h3></div>
        <input value={objectiveDraft.objectiveKey} onChange={(event) => setObjectiveDraft((current) => ({ ...current, objectiveKey: event.target.value }))} placeholder="目标键，例如 RETENTION" pattern="[A-Za-z0-9_]+" required />
        <input value={objectiveDraft.name} onChange={(event) => setObjectiveDraft((current) => ({ ...current, name: event.target.value }))} placeholder="目标名称" required />
        <input value={objectiveDraft.metric} onChange={(event) => setObjectiveDraft((current) => ({ ...current, metric: event.target.value }))} placeholder="受控指标 ID" pattern="[A-Za-z0-9_]+" required />
        <select value={objectiveDraft.direction} onChange={(event) => setObjectiveDraft((current) => ({ ...current, direction: event.target.value as typeof current.direction }))}><option value="INCREASE">提升</option><option value="DECREASE">降低</option><option value="TARGET">达到目标值</option></select>
        {objectiveDraft.direction === "TARGET" && <input type="number" step="any" value={objectiveDraft.targetValue} onChange={(event) => setObjectiveDraft((current) => ({ ...current, targetValue: event.target.value }))} placeholder="目标值" required />}
        <button className="small-approve" disabled={busy} type="submit">创建目标</button>
      </form>
      <form className="decision-setup" onSubmit={(event) => void submitPolicy(event)}>
        <div><span className="panel-kicker">POLICY</span><h3>新建受控策略</h3></div>
        <select value={policyDraft.objectiveId} onChange={(event) => setPolicyDraft((current) => ({ ...current, objectiveId: event.target.value }))} required><option value="">选择经营目标</option>{data.objectives.map((objective) => <option key={objective.id} value={objective.id}>{objective.name} · {objective.metric}</option>)}</select>
        <input value={policyDraft.strategyKey} onChange={(event) => setPolicyDraft((current) => ({ ...current, strategyKey: event.target.value }))} placeholder="策略键，例如 RETENTION_ALERT" pattern="[A-Za-z0-9_]+" required />
        <select value={policyDraft.mode} onChange={(event) => setPolicyDraft((current) => ({ ...current, mode: event.target.value as StrategyPolicy["mode"] }))}><option value="SHADOW">SHADOW（建议先用）</option><option value="ADVISORY">ADVISORY（人工审批）</option><option value="AUTONOMOUS">AUTONOMOUS（低风险自治）</option><option value="REPLAY">REPLAY</option><option value="DISABLED">DISABLED</option></select>
        <select value={policyDraft.riskLevel} onChange={(event) => setPolicyDraft((current) => ({ ...current, riskLevel: event.target.value as StrategyPolicy["risk_level"] }))}>{["L0", "L1", "L2", "L3", "L4"].map((level) => <option key={level} value={level}>{level}</option>)}</select>
        <select value={policyDraft.action} onChange={(event) => setPolicyDraft((current) => ({ ...current, action: event.target.value }))}><option value="CREATE_OPERATIONS_TASK">创建运营任务</option><option value="CREATE_COACH_FOLLOW_UP">创建教练跟进任务</option></select>
        <button className="small-approve" disabled={busy || !policyDraft.objectiveId} type="submit">创建策略</button>
      </form>
      <form className="decision-setup" onSubmit={(event) => void submitExperiment(event)}>
        <div><span className="panel-kicker">EXPERIMENT</span><h3>新建效果实验</h3></div>
        <select value={experimentDraft.policyId} onChange={(event) => setExperimentDraft((current) => ({ ...current, policyId: event.target.value }))} required><option value="">选择策略</option>{data.policies.filter((policy) => policy.status === "ACTIVE").map((policy) => <option key={policy.id} value={policy.id}>{policy.objective_name} · {policy.strategy_key}</option>)}</select>
        <input value={experimentDraft.name} onChange={(event) => setExperimentDraft((current) => ({ ...current, name: event.target.value }))} placeholder="实验名称" required />
        <input type="number" min="1" max="99" value={experimentDraft.treatmentPercent} onChange={(event) => setExperimentDraft((current) => ({ ...current, treatmentPercent: event.target.value }))} aria-label="处理组比例" required />
        <input type="number" min="1" max="365" value={experimentDraft.observationDays} onChange={(event) => setExperimentDraft((current) => ({ ...current, observationDays: event.target.value }))} aria-label="观察天数" required />
        <button className="small-approve" disabled={busy || !experimentDraft.policyId} type="submit">创建实验</button>
      </form>
    </div>
    <div className="decision-stats">
      <div><span>经营目标</span><strong>{data.objectives.length}</strong></div>
      <div><span>有效策略</span><strong>{data.policies.filter((item) => item.status === "ACTIVE").length}</strong></div>
      <div><span>待审批</span><strong>{waiting}</strong></div>
      <div><span>执行中</span><strong>{running}</strong></div>
    </div>
    <div className="decision-layout">
      <div className="decision-column">
        <div className="data-card-head"><div><span className="panel-kicker">DECISION CASES</span><h3>决策案例</h3></div><span className="data-count">{data.cases.length}</span></div>
        <div className="decision-list">{data.cases.length ? data.cases.map((item) => <button key={item.id} className={`decision-case ${selected?.id === item.id ? "selected" : ""}`} onClick={() => void openCase(item)} disabled={busy}><div><strong>{item.title}</strong><p>{new Date(item.created_at).toLocaleString("zh-CN")}</p></div><span className={`status-chip ${["EXECUTION_FAILED", "POLICY_REJECTED"].includes(item.status) ? "danger" : ""}`}>{item.status}</span></button>) : <EmptyRow text="暂无经营决策案例" />}</div>
      </div>
      <div className="decision-detail">
        {selected ? <><div className="data-card-head"><div><span className="panel-kicker">CASE DETAIL</span><h3>{selected.title}</h3><p>风险 {selected.risk_level} · {selected.status}</p></div>{selected.status === "EVALUATED" && <button className="small-approve" disabled={busy} onClick={() => void closeCase()}>复核并结案</button>}</div><div className="detail-grid"><div><span>案例 ID</span><strong>{selected.id}</strong></div><div><span>策略 ID</span><strong>{selected.policy_id}</strong></div><div><span>异常 ID</span><strong>{selected.anomaly_id}</strong></div><div><span>有效期</span><strong>{new Date(selected.expires_at).toLocaleString("zh-CN")}</strong></div></div><div className="diagnosis-box"><span className="panel-kicker">DIAGNOSIS</span><strong>{String(selected.diagnosis.summary ?? "诊断证据待生成")}</strong><p>{String(selected.diagnosis.causality_notice ?? "诊断不会将相关性描述为因果。")}</p></div><div className="decision-options"><span className="panel-kicker">OPTIONS</span>{selected.options?.map((option) => <article key={option.id} className={`decision-option ${selected.selected_option_id === option.id ? "selected" : ""}`}><div><strong>{option.title}</strong><p>{option.action_type} · 置信度 {(option.confidence * 100).toFixed(1)}%</p></div><div className="option-score"><span>综合分</span><b>{option.score.toFixed(2)}</b></div>{selected.status === "WAITING_APPROVAL" && <div className="row-actions"><button className="small-reject" disabled={busy} onClick={() => void decide(selected, option, "REJECT")}>拒绝</button><button className="small-approve" disabled={busy} onClick={() => void decide(selected, option, "APPROVE")}>批准执行</button></div>}</article>)}</div><div className="case-section"><span className="panel-kicker">ACTION RUNS</span>{selected.actions?.length ? selected.actions.map((action) => <div className="case-line" key={action.id}><div><strong>{action.action_type}</strong><p>{action.last_error_code ?? `已尝试 ${action.attempt_count} 次`}</p></div><div className="row-actions"><span className={`status-chip ${action.status === "FINAL_FAILED" ? "danger" : ""}`}>{action.status}</span>{action.status === "FINAL_FAILED" && <button className="small-approve" disabled={busy} onClick={() => void updateAction(action, "retry")}>重试</button>}{["QUEUED", "RETRYABLE_FAILED"].includes(action.status) && <button className="small-reject" disabled={busy} onClick={() => void updateAction(action, "cancel")}>取消</button>}</div></div>) : <EmptyRow text="暂无动作" />}</div><div className="case-section"><span className="panel-kicker">OUTCOME EFFECTS</span>{effects.length ? effects.map((effect) => <div className="case-line" key={`${effect.metric}:${effect.window_start}`}><div><strong>{effect.metric}</strong><p>处理组 {effect.treatment_value}（n={effect.treatment_samples}） · 对照组 {effect.control_value}（n={effect.control_samples}）</p></div><b>{effect.relative_effect_percent == null ? `${effect.absolute_effect}` : `${effect.relative_effect_percent}%`}</b></div>) : <EmptyRow text="尚无可比较效果" />}</div><div className="case-section"><span className="panel-kicker">AUDIT TIMELINE</span>{selected.events?.map((event) => <div className="case-line" key={event.id}><div><strong>{event.to_status}</strong><p>{event.reason_code} · {event.actor_id}</p></div><small>{new Date(event.created_at).toLocaleString("zh-CN")}</small></div>)}</div></> : <div className="empty-page compact-empty"><span>◎</span><h2>选择一个决策案例</h2><p>查看异常证据、候选方案、风险和审批状态。</p></div>}
      </div>
    </div>
    <div className="decision-bottom-grid">
      <DataCard title="策略模式" kicker="POLICIES" count={data.policies.length}><div className="data-list">{data.policies.slice(0, 8).map((policy) => <div className="data-row" key={policy.id}><div><strong>{policy.objective_name}</strong><p>{policy.strategy_key} · v{policy.version}</p></div><div className="row-actions"><span className="status-chip">{policy.mode}</span><span className="status-chip">{policy.risk_level}</span></div></div>)}</div></DataCard>
      <DataCard title="动作执行" kicker="ACTION RUNS" count={data.actions.length}><div className="data-list">{data.actions.slice(0, 8).map((action) => <div className="data-row" key={action.id}><div><strong>{action.action_type}</strong><p>{action.last_error_code ?? `尝试 ${action.attempt_count} 次`}</p></div><span className={`status-chip ${action.status === "FINAL_FAILED" ? "danger" : ""}`}>{action.status}</span></div>)}</div></DataCard>
      <DataCard title="效果实验" kicker="EXPERIMENTS" count={data.experiments.length}><div className="data-list">{data.experiments.slice(0, 8).map((experiment) => <div className="data-row" key={experiment.id}><div><strong>{experiment.name}</strong><p>{experiment.primary_metric} · 处理组 {experiment.treatment_percent}% · {experiment.observation_days} 天</p></div><div className="row-actions"><span className="status-chip">{experiment.status}</span>{["DRAFT", "STOPPED"].includes(experiment.status) && <button className="small-approve" disabled={busy} onClick={() => void changeExperimentStatus(experiment, "RUNNING")}>启动</button>}{experiment.status === "RUNNING" && <><button className="outline-button" disabled={busy} onClick={() => void changeExperimentStatus(experiment, "STOPPED")}>暂停</button><button className="small-approve" disabled={busy} onClick={() => void changeExperimentStatus(experiment, "COMPLETED")}>完成</button></>}</div></div>)}</div></DataCard>
    </div>
  </section>;
}

type DataCenterState = {
  memoryInbox: MemoryCandidateInbox | null;
  memories: FitnessMemory[];
  notifications: InAppNotification[];
  preference: NotificationPreference | null;
  metricCatalog: OperationsMetricCatalog | null;
  audits: OperationsAuditPage | null;
  knowledgeJobs: KnowledgeJob[];
  reindexJobs: ReindexJob[];
  deliveryAttempts: NotificationDeliveryAttempt[];
};

const emptyDataCenter: DataCenterState = {
  memoryInbox: null,
  memories: [],
  notifications: [],
  preference: null,
  metricCatalog: null,
  audits: null,
  knowledgeJobs: [],
  reindexJobs: [],
  deliveryAttempts: [],
};

function DataCenterView({ catalog, onOpenAgent }: { catalog: CapabilityCatalog | null; onOpenAgent: (notification: InAppNotification) => void }) {
  const [data, setData] = useState<DataCenterState>(emptyDataCenter);
  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState<string[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [busyId, setBusyId] = useState("");
  const [selectedJob, setSelectedJob] = useState<KnowledgeJob | null>(null);
  const [selectedReport, setSelectedReport] = useState<KnowledgeReviewReport | null>(null);
  const [governanceBusy, setGovernanceBusy] = useState(false);
  const [governanceMessage, setGovernanceMessage] = useState("");
  const [reviewComment, setReviewComment] = useState("");
  const [editingMemory, setEditingMemory] = useState<FitnessMemory | null>(null);
  const [memoryValue, setMemoryValue] = useState("");
  const [memoryUnit, setMemoryUnit] = useState("");
  const decisionIds = useRef(new Map<string, string>());
  const isAdmin = Boolean(catalog?.roles.some((role) => ["SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "ADMIN", "SUPER_ADMIN"].includes(role)));

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      const next: DataCenterState = { ...emptyDataCenter };
      const nextErrors: string[] = [];
      const userResults = await Promise.allSettled([getMemoryCandidateInbox(), getMemories(), getNotifications(), getNotificationPreference()]);
      const [inbox, memories, notifications, preference] = userResults;
      if (inbox.status === "fulfilled") next.memoryInbox = inbox.value;
      else nextErrors.push(`Memory 收件箱：${formatError(inbox.reason)}`);
      if (memories.status === "fulfilled") next.memories = memories.value;
      else nextErrors.push(`正式 Memory：${formatError(memories.reason)}`);
      if (notifications.status === "fulfilled") next.notifications = notifications.value;
      else nextErrors.push(`通知中心：${formatError(notifications.reason)}`);
      if (preference.status === "fulfilled") next.preference = preference.value;
      else nextErrors.push(`通知偏好：${formatError(preference.reason)}`);

      if (isAdmin) {
        const adminResults = await Promise.allSettled([getOperationsMetricCatalog(), getOperationsAudits(), getKnowledgeJobs(), getReindexJobs(), getNotificationDeliveryAttempts()]);
        const [metricCatalog, audits, knowledgeJobs, reindexJobs, deliveryAttempts] = adminResults;
        if (metricCatalog.status === "fulfilled") next.metricCatalog = metricCatalog.value;
        else nextErrors.push(`经营指标目录：${formatError(metricCatalog.reason)}`);
        if (audits.status === "fulfilled") next.audits = audits.value;
        else nextErrors.push(`经营查询审计：${formatError(audits.reason)}`);
        if (knowledgeJobs.status === "fulfilled") next.knowledgeJobs = knowledgeJobs.value;
        else nextErrors.push(`知识任务：${formatError(knowledgeJobs.reason)}`);
        if (reindexJobs.status === "fulfilled") next.reindexJobs = reindexJobs.value;
        else nextErrors.push(`索引重建任务：${formatError(reindexJobs.reason)}`);
        if (deliveryAttempts.status === "fulfilled") next.deliveryAttempts = deliveryAttempts.value;
        else nextErrors.push(`通知投递：${formatError(deliveryAttempts.reason)}`);
      }
      if (!cancelled) {
        setData(next);
        setErrors(nextErrors);
        setLoading(false);
      }
    }
    if (catalog) void load();
    else setLoading(false);
    return () => { cancelled = true; };
  }, [catalog, isAdmin, refreshKey]);

  function decisionId(prefix: string, id: string): string {
    const key = `${prefix}:${id}`;
    const existing = decisionIds.current.get(key);
    if (existing) return existing;
    const created = stableId(prefix);
    decisionIds.current.set(key, created);
    return created;
  }

  async function decideCandidate(id: string, decision: "APPROVE" | "REJECT") {
    setBusyId(id);
    try {
      await decideMemoryCandidate(id, decision, decisionId(decision.toLowerCase(), id));
      setRefreshKey((value) => value + 1);
    } catch (reason: unknown) {
      setErrors((current) => [...current, formatError(reason)]);
    } finally {
      setBusyId("");
    }
  }

  async function readNotification(notification: InAppNotification) {
    if (notification.read_at || busyId === notification.id) return;
    setBusyId(notification.id);
    try {
      const updated = await markNotificationRead(notification.id);
      setData((current) => ({ ...current, notifications: current.notifications.map((item) => item.id === updated.id ? updated : item) }));
    } catch (reason: unknown) {
      setErrors((current) => [...current, formatError(reason)]);
    } finally {
      setBusyId("");
    }
  }

  async function openProactiveNotification(notification: InAppNotification) {
    if (!notification.proactive_candidate_id || busyId === notification.id) return;
    setBusyId(notification.id);
    try {
      const updated = notification.read_at ? notification : await markNotificationRead(notification.id);
      await respondToProactiveNotification(
        notification.proactive_candidate_id,
        "OPENED",
        notification.id,
      );
      setData((current) => ({
        ...current,
        notifications: current.notifications.map((item) => item.id === updated.id ? updated : item),
      }));
      onOpenAgent(notification);
    } catch (reason: unknown) {
      setErrors((current) => [...current, formatError(reason)]);
    } finally {
      setBusyId("");
    }
  }

  async function respondToSuggestion(
    notification: InAppNotification,
    responseType: "SNOOZED" | "MUTED" | "HELPFUL" | "NOT_HELPFUL",
    responseValue?: string,
  ) {
    if (!notification.proactive_candidate_id || busyId === notification.id) return;
    setBusyId(notification.id);
    try {
      await respondToProactiveNotification(
        notification.proactive_candidate_id,
        responseType,
        notification.id,
        responseValue,
      );
      const updated = notification.read_at ? notification : await markNotificationRead(notification.id);
      setData((current) => ({
        ...current,
        notifications: current.notifications.map((item) => item.id === updated.id ? updated : item),
      }));
    } catch (reason: unknown) {
      setErrors((current) => [...current, formatError(reason)]);
    } finally {
      setBusyId("");
    }
  }

  async function toggleNotifications() {
    if (!data.preference || busyId === "notification-preference") return;
    setBusyId("notification-preference");
    try {
      const updated = await saveNotificationPreference(!data.preference.enabled);
      setData((current) => ({ ...current, preference: updated }));
    } catch (reason: unknown) {
      setErrors((current) => [...current, formatError(reason)]);
    } finally {
      setBusyId("");
    }
  }

  async function revokeActiveMemory(memory: FitnessMemory) {
    if (busyId === memory.id) return;
    setBusyId(memory.id);
    try {
      const updated = await revokeMemory(memory.id, memory.version, decisionId("revoke-memory", memory.id));
      setData((current) => ({ ...current, memories: current.memories.map((item) => item.id === updated.id ? updated : item) }));
    } catch (reason: unknown) {
      setErrors((current) => [...current, formatError(reason)]);
    } finally {
      setBusyId("");
    }
  }

  function beginMemoryCorrection(memory: FitnessMemory) {
    const value = typeof memory.content.value === "string" ? memory.content.value : JSON.stringify(memory.content);
    const unit = typeof memory.content.unit === "string" ? memory.content.unit : "";
    setEditingMemory(memory);
    setMemoryValue(value);
    setMemoryUnit(unit);
  }

  async function submitMemoryCorrection(event: FormEvent) {
    event.preventDefault();
    if (!editingMemory || !memoryValue.trim() || busyId === editingMemory.id) return;
    setBusyId(editingMemory.id);
    try {
      const updated = await correctMemory(editingMemory.id, memoryValue.trim(), memoryUnit.trim() || null, editingMemory.version, decisionId("correct-memory", editingMemory.id));
      setData((current) => ({ ...current, memories: current.memories.map((item) => item.id === updated.id ? updated : item) }));
      setEditingMemory(null);
    } catch (reason: unknown) {
      setErrors((current) => [...current, formatError(reason)]);
    } finally {
      setBusyId("");
    }
  }

  async function openKnowledgeJob(job: KnowledgeJob) {
    setGovernanceBusy(true);
    setGovernanceMessage("");
    try {
      const [detail, report] = await Promise.all([getKnowledgeJob(job.id), getKnowledgeReviewReport(job.id)]);
      setSelectedJob(detail);
      setSelectedReport(report);
      setReviewComment("");
    } catch (reason: unknown) {
      setGovernanceMessage(formatError(reason));
    } finally {
      setGovernanceBusy(false);
    }
  }

  async function reviewKnowledgeJob(action: "approve" | "reject" | "retry") {
    if (!selectedJob || governanceBusy) return;
    if (action === "approve" && selectedJob.status !== "PENDING_REVIEW") {
      setGovernanceMessage("当前任务状态不允许批准");
      return;
    }
    if (action === "reject" && selectedJob.status !== "PENDING_REVIEW") {
      setGovernanceMessage("当前任务状态不允许拒绝");
      return;
    }
    if (action === "retry" && selectedJob.status !== "FAILED") {
      setGovernanceMessage("当前任务状态不允许重试");
      return;
    }
    if (action === "reject" && reviewComment.trim().length < 1) {
      setGovernanceMessage("拒绝知识任务必须填写原因");
      return;
    }
    setGovernanceBusy(true);
    setGovernanceMessage("");
    try {
      const updated = action === "approve"
        ? await approveKnowledgeJob(selectedJob.id, reviewComment.trim())
        : action === "reject"
          ? await rejectKnowledgeJob(selectedJob.id, reviewComment.trim())
          : await retryKnowledgeJob(selectedJob.id);
      setSelectedJob(updated);
      setRefreshKey((value) => value + 1);
      setGovernanceMessage(action === "approve" ? "已批准，任务已进入后台索引队列" : action === "reject" ? "已拒绝，决定已写入审计" : "已重新排队");
    } catch (reason: unknown) {
      setGovernanceMessage(formatError(reason));
    } finally {
      setGovernanceBusy(false);
    }
  }

  async function uploadKnowledge(form: FormData) {
    setGovernanceBusy(true);
    setGovernanceMessage("");
    try {
      const created = await uploadKnowledgeDocument(form);
      setSelectedJob(created);
      setSelectedReport(null);
      setRefreshKey((value) => value + 1);
      setGovernanceMessage(`已接收「${created.original_filename}」，等待质量报告和审核`);
    } catch (reason: unknown) {
      setGovernanceMessage(formatError(reason));
    } finally {
      setGovernanceBusy(false);
    }
  }

  if (!catalog) return <div className="empty-page"><span>◈</span><h2>暂时无法加载数据中心</h2><p>请先配置 AgentContext，页面会根据当前角色和机构范围加载数据。</p></div>;
  return (
    <section className="data-page">
      <div className="catalog-intro">
        <div><span className="panel-kicker">SECURE DATA CENTER</span><h2>业务数据与治理状态</h2><p>这里展示服务端已经允许当前身份读取的资料。所有修改仍经过版本校验、幂等处理和审计。</p></div>
        <button className="outline-button" onClick={() => setRefreshKey((value) => value + 1)} disabled={loading}>{loading ? "刷新中…" : "刷新数据"}</button>
      </div>
      {errors.length > 0 && <div className="partial-warning"><strong>部分数据未加载</strong><span>{errors.join("；")}</span></div>}
      {editingMemory && <form className="memory-edit" onSubmit={submitMemoryCorrection}><div><span className="panel-kicker">MEMORY CORRECTION</span><h3>纠正「{editingMemory.memory_key}」</h3></div><input value={memoryValue} onChange={(event) => setMemoryValue(event.target.value)} placeholder="新的值" required /><input value={memoryUnit} onChange={(event) => setMemoryUnit(event.target.value)} placeholder="单位（可选）" /><button className="small-approve" type="submit" disabled={!memoryValue.trim() || busyId === editingMemory.id}>保存纠正</button><button className="outline-button" type="button" onClick={() => setEditingMemory(null)}>取消</button></form>}
      {isAdmin && <AdminGovernancePanel jobs={data.knowledgeJobs} deliveryAttempts={data.deliveryAttempts} selectedJob={selectedJob} report={selectedReport} busy={governanceBusy} message={governanceMessage} reviewComment={reviewComment} onCommentChange={setReviewComment} onOpenJob={(job) => void openKnowledgeJob(job)} onReview={(action) => void reviewKnowledgeJob(action)} onUpload={(form) => void uploadKnowledge(form)} />}
      <div className="data-grid">
        <DataCard title="Memory 待确认" kicker="MEMORY INBOX" count={data.memoryInbox?.items.length ?? 0}><div className="data-list">{data.memoryInbox?.items.length ? data.memoryInbox.items.map(({ candidate }) => <div className="data-row" key={candidate.id}><div><strong>{candidate.memory_key}</strong><p>{candidate.value}{candidate.unit ? ` ${candidate.unit}` : ""}</p></div><div className="row-actions"><button className="small-reject" disabled={busyId === candidate.id} onClick={() => void decideCandidate(candidate.id, "REJECT")}>拒绝</button><button className="small-approve" disabled={busyId === candidate.id} onClick={() => void decideCandidate(candidate.id, "APPROVE")}>{busyId === candidate.id ? "处理中…" : "确认"}</button></div></div>) : <EmptyRow text="暂无待确认候选" />}</div></DataCard>
        <DataCard title="已确认 Memory" kicker="ACTIVE MEMORY" count={data.memories.length}><div className="data-list">{data.memories.length ? data.memories.map((memory) => <div className="data-row" key={memory.id}><div><strong>{memory.memory_key}</strong><p>{JSON.stringify(memory.content)}</p></div><div className="row-actions"><span className="status-chip">{memory.status}</span><button className="outline-button" disabled={busyId === memory.id} onClick={() => beginMemoryCorrection(memory)}>纠正</button><button className="small-reject" disabled={busyId === memory.id} onClick={() => void revokeActiveMemory(memory)}>撤销</button></div></div>) : <EmptyRow text="暂无有效 Memory" />}</div></DataCard>
        <DataCard title="通知中心" kicker="IN-APP NOTIFICATIONS" count={data.notifications.filter((item) => !item.read_at).length}>
          <div className="data-list">{data.notifications.length ? data.notifications.slice(0, 8).map((notification) => <NotificationItem key={notification.id} notification={notification} busy={busyId === notification.id} onRead={() => void readNotification(notification)} onOpenAgent={() => void openProactiveNotification(notification)} onRespond={(responseType, responseValue) => void respondToSuggestion(notification, responseType, responseValue)} />) : <EmptyRow text="暂无站内通知" />}</div>
        </DataCard>
        <DataCard title="通知偏好" kicker="NOTIFICATION PREFERENCES" count={data.preference?.enabled ? 1 : 0}><div className="preference-row"><div><strong>站内通知</strong><p>{data.preference ? (data.preference.enabled ? "已开启" : "已关闭") : "未加载"} · {data.preference?.timezone ?? ""}</p></div><button className="small-approve" disabled={!data.preference || busyId === "notification-preference"} onClick={() => void toggleNotifications()}>{data.preference?.enabled ? "关闭" : "开启"}</button></div></DataCard>
        <DataCard title="知识任务" kicker="KNOWLEDGE GOVERNANCE" count={data.knowledgeJobs.length} adminOnly={!isAdmin}><div className="data-list">{data.knowledgeJobs.length ? data.knowledgeJobs.slice(0, 8).map((job) => <div className="data-row" key={job.id}><div><strong>{job.title || job.original_filename}</strong><p>{job.document_type} · 安全：{job.malware_status}</p></div><div className="row-actions"><span className="status-chip">{humanStatus(job.status)}</span>{isAdmin && <button className="small-approve" onClick={() => void openKnowledgeJob(job)}>审核</button>}</div></div>) : <EmptyRow text={isAdmin ? "暂无知识任务" : "仅管理员可查看"} />}</div></DataCard>
        {isAdmin && <>
          <DataCard title="经营指标目录" kicker="OPERATIONS CATALOG" count={data.metricCatalog?.items.length ?? 0}><div className="data-list">{data.metricCatalog?.items.length ? data.metricCatalog.items.slice(0, 8).map((metric) => <div className="data-row" key={metric.id}><div><strong>{metric.label}</strong><p>{metric.description}</p></div><span className="status-chip">{metric.supported_buckets.join(" / ")}</span></div>) : <EmptyRow text="暂无指标目录" />}</div></DataCard>
          <DataCard title="经营查询审计" kicker="QUERY AUDITS" count={data.audits?.items.length ?? 0}><div className="data-list">{data.audits?.items.length ? data.audits.items.slice(0, 8).map((audit) => <div className="data-row" key={audit.id}><div><strong>{audit.metric_definition.label}</strong><p>{audit.bucket} · {audit.comparison_role} · {formatDate(audit.created_at)}</p></div><span className="status-chip">{humanStatus(audit.status)}</span></div>) : <EmptyRow text="暂无经营查询审计" />}</div></DataCard>
          <DataCard title="索引重建" kicker="REINDEX JOBS" count={data.reindexJobs.length}><div className="data-list">{data.reindexJobs.length ? data.reindexJobs.slice(0, 8).map((job) => <div className="data-row" key={job.id}><div><strong>{job.id.slice(0, 12)}…</strong><p>{job.processed_documents}/{job.total_documents} 已处理 · 失败 {job.failed_documents}</p></div><span className="status-chip">{humanStatus(job.status)}</span></div>) : <EmptyRow text="暂无索引重建任务" />}</div></DataCard>
        </>}
      </div>
    </section>
  );
}

type SuggestionResponse = "SNOOZED" | "MUTED" | "HELPFUL" | "NOT_HELPFUL";

function NotificationItem({
  notification,
  busy,
  onRead,
  onOpenAgent,
  onRespond,
}: {
  notification: InAppNotification;
  busy: boolean;
  onRead: () => void;
  onOpenAgent: () => void;
  onRespond: (responseType: SuggestionResponse, responseValue?: string) => void;
}) {
  const proactive = Boolean(notification.proactive_candidate_id);
  return (
    <article className={`notification-row ${notification.read_at ? "read" : "unread"}`}>
      <button className="notification-main" type="button" onClick={onRead} disabled={busy}>
        <span className="notification-dot" />
        <span>
          <strong>{notification.title}</strong>
          <p>{notification.body}</p>
          <small>{formatDate(notification.created_at)}</small>
        </span>
      </button>
      {proactive && (
        <div className="notification-actions" aria-label="主动建议操作">
          {notification.cta_type && <button className="small-approve" type="button" disabled={busy} onClick={onOpenAgent}>打开助手</button>}
          <button className="outline-button" type="button" disabled={busy} onClick={() => onRespond("SNOOZED", "1440")}>明天提醒</button>
          <button className="outline-button" type="button" disabled={busy} onClick={() => onRespond("HELPFUL")}>有帮助</button>
          <button className="outline-button" type="button" disabled={busy} onClick={() => onRespond("NOT_HELPFUL")}>没帮助</button>
          <button className="small-reject" type="button" disabled={busy} onClick={() => onRespond("MUTED")}>不再提醒此类</button>
        </div>
      )}
    </article>
  );
}

function AdminGovernancePanel({
  jobs,
  deliveryAttempts,
  selectedJob,
  report,
  busy,
  message,
  reviewComment,
  onCommentChange,
  onOpenJob,
  onReview,
  onUpload,
}: {
  jobs: KnowledgeJob[];
  deliveryAttempts: NotificationDeliveryAttempt[];
  selectedJob: KnowledgeJob | null;
  report: KnowledgeReviewReport | null;
  busy: boolean;
  message: string;
  reviewComment: string;
  onCommentChange: (value: string) => void;
  onOpenJob: (job: KnowledgeJob) => void;
  onReview: (action: "approve" | "reject" | "retry") => void;
  onUpload: (form: FormData) => void;
}) {
  return <section className="governance-panel"><div className="governance-heading"><div><span className="panel-kicker">ADMIN GOVERNANCE</span><h2>知识审核与投递运维</h2><p>上传资料先进入待审核任务；质量报告和安全状态由服务端决定，前端不能绕过门禁强制发布。</p></div><span className="version-badge">{jobs.length} 个任务</span></div><div className="governance-grid"><form className="governance-card upload-card" onSubmit={(event) => { event.preventDefault(); const form = event.currentTarget; const payload = new FormData(form); payload.set("effective_from", new Date().toISOString()); onUpload(payload); form.reset(); }}><div className="data-card-head"><div><span className="panel-kicker">UPLOAD</span><h3>提交知识资料</h3></div></div><label>文件<input name="file" type="file" accept=".pdf,.docx,.xlsx,.md,.txt" required /></label><label>标题<input name="title" required maxLength={256} placeholder="例如：高温运动安全指南" /></label><label>来源地址<input name="source_uri" required minLength={13} placeholder="https://example.org/source" /></label><div className="form-row"><label>文档类型<select name="document_type" defaultValue="FITNESS_GUIDE"><option value="FITNESS_GUIDE">健身指南</option><option value="TRAINING_MANUAL">训练手册</option><option value="NUTRITION">营养资料</option><option value="OTHER">其他</option></select></label><label>风险级别<select name="risk_level" defaultValue="NORMAL"><option value="NORMAL">普通</option><option value="CAUTION">需注意</option><option value="MEDICAL">医疗相关</option></select></label></div><div className="form-row"><label>可见范围<select name="visibility" defaultValue="GLOBAL"><option value="GLOBAL">全平台</option><option value="ORGANIZATION">机构内</option><option value="PRIVATE">仅提交者</option></select></label><label>机构 ID（可选）<input name="organization_id" placeholder="组织管理员可留空" /></label></div><button className="workspace-primary" type="submit" disabled={busy}>{busy ? "处理中…" : "上传并进入审核"}</button></form><div className="governance-card"><div className="data-card-head"><div><span className="panel-kicker">REVIEW QUEUE</span><h3>审核任务</h3></div><span className="data-count">{jobs.length}</span></div><div className="data-list">{jobs.length ? jobs.slice(0, 10).map((job) => <button className={`governance-job ${selectedJob?.id === job.id ? "selected" : ""}`} key={job.id} onClick={() => onOpenJob(job)}><div><strong>{job.title || job.original_filename}</strong><p>{job.document_type} · {job.original_filename}</p></div><span className="status-chip">{humanStatus(job.status)}</span></button>) : <EmptyRow text="暂无知识任务" />}</div></div></div>{selectedJob && <div className="governance-detail"><div className="data-card-head"><div><span className="panel-kicker">SELECTED JOB</span><h3>{selectedJob.title || selectedJob.original_filename}</h3><p>{selectedJob.original_filename} · {selectedJob.content_type} · {formatBytes(selectedJob.size_bytes)}</p></div><span className="status-chip">{humanStatus(selectedJob.status)}</span></div><div className="detail-grid"><div><span>安全扫描</span><strong>{selectedJob.malware_status} / {selectedJob.safety_status}</strong></div><div><span>解析哈希</span><strong>{selectedJob.content_sha256.slice(0, 16)}…</strong></div><div><span>审核人</span><strong>{selectedJob.reviewer_id ?? "尚未审核"}</strong></div><div><span>错误信息</span><strong>{selectedJob.error_message ?? "无"}</strong></div></div>{report && <div className="report-box"><div className="report-title"><strong>解析质量报告</strong><span className={`status-chip ${report.status === "BLOCKED" ? "danger" : ""}`}>{humanStatus(report.status)}</span></div><div className="metric-list">{Object.entries(report.quality_metrics).slice(0, 8).map(([key, value]) => <span key={key}><b>{key}</b>{String(value ?? "—")}</span>)}</div>{report.warnings.length > 0 && <p className="report-warning">警告：{report.warnings.join("；")}</p>}{report.findings.length > 0 && <div className="finding-list">{report.findings.slice(0, 6).map((finding) => <p key={`${finding.code}-${finding.message}`}><b>{finding.severity}</b> {finding.message}{finding.pages.length ? `（页码：${finding.pages.join(", ")}）` : ""}</p>)}</div>}<small>解析器：{report.parser_name} {report.parser_version} · 管线：{report.parser_pipeline_version} · 页数画像：{report.page_profiles.length}</small></div>}<div className="review-actions"><textarea value={reviewComment} onChange={(event) => onCommentChange(event.target.value)} placeholder="审批备注；拒绝时必填" maxLength={500} /><div><button className="small-reject" disabled={busy} onClick={() => onReview("reject")}>拒绝</button>{selectedJob.status === "FAILED" && <button className="outline-button" disabled={busy} onClick={() => onReview("retry")}>重试</button>}{selectedJob.status === "PENDING_REVIEW" && <button className="small-approve" disabled={busy || report?.can_admin_approve === false} onClick={() => onReview("approve")}>批准并索引</button>}</div></div></div>}{message && <div className="governance-message">{message}</div>}<div className="delivery-strip"><div><span className="panel-kicker">DELIVERY OPS</span><strong>通知投递摘要</strong></div><span>{deliveryAttempts.length} 条记录 · 仅 IN_APP</span>{deliveryAttempts.slice(0, 5).map((attempt) => <span className="delivery-item" key={attempt.id}><b>{attempt.notification_type}</b> {humanStatus(attempt.status)}{attempt.error_code ? ` · ${attempt.error_code}` : ""}</span>)}</div></section>;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function DataCard({ title, kicker, count, adminOnly, children }: { title: string; kicker: string; count: number; adminOnly?: boolean; children: React.ReactNode }) {
  return <article className="data-card"><div className="data-card-head"><div><span className="panel-kicker">{kicker}</span><h3>{title}</h3></div><span className="data-count">{adminOnly ? "管理员" : count}</span></div>{children}</article>;
}

function EmptyRow({ text }: { text: string }) {
  return <div className="empty-row">{text}</div>;
}

function humanStatus(status: string): string {
  const values: Record<string, string> = { PENDING_REVIEW: "待审核", QUEUED: "排队中", INDEXING: "索引中", SUCCEEDED: "成功", FAILED: "失败", REJECTED: "已拒绝", APPROVED: "已批准", PUBLISHED: "已发布", RUNNING: "执行中", COMPLETED: "已完成", PASS: "通过", REVIEW_REQUIRED: "需人工审核", BLOCKED: "已拦截", STARTED: "已开始", RETRYABLE_FAILED: "可重试失败", FINAL_FAILED: "最终失败", DEFERRED: "已延迟", SUPPRESSED: "已抑制" };
  return values[status] ?? status;
}

function formatDate(value: string | null): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

export default App;
