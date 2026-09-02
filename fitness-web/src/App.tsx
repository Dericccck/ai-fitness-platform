import { useEffect, useMemo, useState } from "react";
import { ApiError, decideConfirmation, getCapabilities, getConfirmation, sendChat } from "./api";
import type { Capability, CapabilityCatalog, ChatMessage, ChatResponse, Confirmation } from "./types";
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
  const [activeView, setActiveView] = useState<"chat" | "capabilities">("chat");
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: stableId("assistant"),
      role: "assistant",
      content: "你好，我是健身工作台。你可以询问训练动作、课程、合同、预约或已发布训练计划。涉及写入的操作会先展示确认卡片。",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [error, setError] = useState("");
  const conversationId = useMemo(initialConversationId, []);

  useEffect(() => {
    getCapabilities()
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
    setMessages((current) => [...current, { id: stableId("user"), role: "user", content: message }]);
    setLoading(true);
    try {
      const result = await sendChat(conversationId, message);
      setMessages((current) => [
        ...current,
        {
          id: stableId("assistant"),
          role: "assistant",
          content: result.answer,
          route: result.route,
          confirmation: result.confirmation_id ? result : undefined,
        },
      ]);
    } catch (reason: unknown) {
      setError(formatError(reason));
    } finally {
      setLoading(false);
    }
  }

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
        <button className={`nav-item ${activeView === "capabilities" ? "active" : ""}`} onClick={() => setActiveView("capabilities")}>
          <span>◫</span> 我的能力
        </button>
        <div className="sidebar-spacer" />
        <div className="security-note"><span className="status-dot" /> 权限由服务端实时校验</div>
        <div className="user-card">
          <div className="avatar">{roles[0]?.slice(0, 1) ?? "?"}</div>
          <div><strong>{roles[0] ? roleNames[roles[0]] ?? roles[0] : "未识别角色"}</strong><span>{loadingCatalog ? "正在读取能力…" : "签名上下文已接入"}</span></div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div><span className="eyebrow">AI FITNESS PLATFORM</span><h1>{activeView === "chat" ? "智能健身助手" : "能力目录"}</h1></div>
          <div className="topbar-meta"><span className="live-pill"><i /> Agent 在线</span><span className="role-pill">{roles.map((role) => roleNames[role] ?? role).join(" / ") || "等待身份"}</span></div>
        </header>

        {error && <div className="error-banner"><strong>请求未完成</strong><span>{error}</span><button onClick={() => setError("")}>×</button></div>}

        {activeView === "chat" ? (
          <section className="chat-layout">
            <div className="chat-panel">
              <div className="panel-heading"><div><span className="panel-kicker">CONVERSATION</span><h2>和你的健身助手聊聊</h2></div><span className="conversation-label">会话已持久化</span></div>
              <div className="message-list">
                {messages.map((message) => <MessageBubble key={message.id} message={message} onDecision={setError} />)}
                {loading && <div className="message-row assistant"><div className="assistant-avatar">F</div><div className="message-bubble typing"><span /><span /><span /></div></div>}
              </div>
              <form className="composer" onSubmit={submit}><textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder="例如：我今天适合做哪些热身动作？" rows={2} disabled={loading} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(event); } }} /><button type="submit" disabled={loading || !input.trim()}>{loading ? "处理中" : "发送"}<span>↗</span></button><small>Enter 发送 · Shift + Enter 换行</small></form>
            </div>
            <aside className="context-panel"><div className="panel-heading compact"><div><span className="panel-kicker">ACCESS</span><h3>当前可用能力</h3></div></div><div className="capability-list">{groups.slice(0, 5).map(([domain, items]) => <div className="capability-group" key={domain}><div className="group-title"><span>{domainIcons[domain] ?? "◇"}</span>{domainNames[domain] ?? domain}<em>{items.length}</em></div><div className="group-items">{items.slice(0, 3).map((item) => <span key={item.id}>{item.display_name}</span>)}</div></div>)}{!catalog && <div className="empty-state">配置 AgentContext 后加载能力目录</div>}</div><button className="text-button" onClick={() => setActiveView("capabilities")}>查看全部能力 <span>→</span></button></aside>
          </section>
        ) : <CapabilitiesView catalog={catalog} />}
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

export default App;
