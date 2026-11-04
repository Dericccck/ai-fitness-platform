import * as SecureStore from "./vault";
import { desktopSimulation } from "./vault";
import { Platform } from "react-native";
import { File, UploadType, type UploadTask } from "expo-file-system";
import type { MediaItem } from "../domain/types";

const SESSION_KEY = "fitness.mobile.session.v1";
const AGENT = "/api/v1/mobile/agent";
type Tokens = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  session_expires_at: number;
  subject: string;
  organization_id: string;
};
type Session = Tokens & { access_expires_at: number };
export type RemoteConversation = {
  conversation_id: string;
  created_at: string;
  updated_at: string;
  legacy_linked?: boolean;
};
export type RemoteLegacyConversation = { thread_id: string; updated_at: string };
export type RecoverableConversation = { conversation_id: string; updated_at: string };
export type RemoteMedia = {
  asset_id: string;
  kind: "image" | "video";
  content_type: string;
  status: string;
};
type RemoteReportBase = {
  schema_version?: string;
  summary: string;
  observations: {
    id: string;
    description: string;
    evidence_type: string;
    time_range: { start_seconds: number; end_seconds: number } | null;
  }[];
  limitations: string[];
  follow_up_question: string | null;
  knowledge_citations: { citation_id: string; title: string; source_page?: number | null }[];
  knowledge_status: string;
};
export type RemoteMovementReport = RemoteReportBase & {
  exercise: { name: string; identification: string };
  assessment: string;
  adjustments: {
    advice: string;
    basis_type: string;
    related_observation_ids: string[];
    citation_ids: string[];
  }[];
};
export type RemoteKnowledgeReport = RemoteReportBase & {
  schema_version: "fitness-knowledge.v1";
  key_points: { text: string; basis_type: "MEDIA" | "GENERAL_GUIDANCE"; citation_ids: string[] }[];
};
export type RemoteReport = RemoteMovementReport | RemoteKnowledgeReport;
export type RemoteMessage = {
  message_id: string;
  run_id: string;
  role: "user" | "assistant";
  status: string;
  schema_version?: string;
  content: string | RemoteReport | null;
  created_at: string | null;
  media: RemoteMedia[];
  source?: "legacy";
};
export type RemoteRun = {
  run_id: string;
  status: string;
  response: { report?: RemoteReport; content_deleted?: boolean } | null;
  error_code: string | null;
};
type UploadPolicy = {
  url: string;
  form_data: Record<string, string>;
  file_field_name: string;
  expires_at: string;
};
type UploadSession = {
  upload_id: string;
  asset_id: string;
  status: string;
  upload_mode?: "direct" | "gateway";
  upload: UploadPolicy | null;
};

function baseUrl(): string {
  const value =
    process.env.EXPO_PUBLIC_AGENT_BASE_URL?.trim().replace(/\/$/, "") || "";
  try {
    const parsed = new URL(value);
    if (
      (parsed.protocol !== "https:" && !(desktopSimulation && parsed.origin === "http://127.0.0.1:8099")) ||
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash ||
      parsed.pathname !== "/"
    )
      throw new Error();
    return parsed.origin;
  } catch {
    throw new Error("尚未配置安全的后端 HTTPS 地址，请联系项目管理员。");
  }
}

export function backendConfigured(): boolean {
  try {
    baseUrl();
    return Platform.OS !== "web" || desktopSimulation;
  } catch {
    return false;
  }
}

async function rawRequest(
  path: string,
  method: string,
  body?: unknown,
  access?: string,
): Promise<any> {
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    path.endsWith("/complete") ? 180000 : 30000,
  );
  try {
    const response = await fetch(baseUrl() + path, {
      method,
      credentials: "omit",
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(access ? { Authorization: `Bearer ${access}` } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok)
      throw new ApiError(
        response.status,
        typeof data.detail === "string" && data.detail.length <= 200
          ? data.detail
          : "服务暂时不可用，请稍后重试。",
      );
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new Error("网络连接未完成，请检查网络后重试。");
  } finally {
    clearTimeout(timeout);
  }
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export class MobileApi {
  private session: Session | null = null;
  private refreshing: Promise<void> | null = null;
  private generation = 0;
  private vault: Promise<void> = Promise.resolve();
  private uploads = new Set<XMLHttpRequest>();
  private nativeUploads = new Set<UploadTask>();
  private listeners = new Set<(loggedIn: boolean) => void>();

  subscribe(listener: (loggedIn: boolean) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
  private notify(): void {
    this.listeners.forEach((listener) => listener(this.loggedIn));
  }

  private async vaultOperation(operation: () => Promise<void>): Promise<void> {
    const next = this.vault.then(operation);
    this.vault = next.catch(() => undefined);
    return next;
  }

  get loggedIn(): boolean {
    return this.session !== null;
  }
  get scope(): string | null {
    return this.session
      ? `${this.session.subject}\u0000${this.session.organization_id}`
      : null;
  }
  get version(): number {
    return this.generation;
  }

  async restore(): Promise<boolean> {
    if (!backendConfigured()) return false;
    const generation = this.generation;
    const raw = await SecureStore.getItemAsync(SESSION_KEY);
    if (generation !== this.generation) return false;
    if (!raw) return false;
    try {
      const session: Session = JSON.parse(raw);
      if (
        !session.access_token ||
        !session.refresh_token ||
        !session.subject ||
        session.session_expires_at * 1000 <= Date.now()
      )
        throw new Error();
      this.session = session;
      this.notify();
      return true;
    } catch {
      await this.vaultOperation(async () => {
        if (generation === this.generation) await SecureStore.deleteItemAsync(SESSION_KEY);
      });
      return false;
    }
  }

  private async save(tokens: Tokens, generation: number): Promise<void> {
    if (
      !tokens.access_token ||
      !tokens.refresh_token ||
      !tokens.subject ||
      !tokens.organization_id ||
      tokens.expires_in <= 0
    )
      throw new Error("登录响应无效。");
    const next = {
      ...tokens,
      access_expires_at: Date.now() + tokens.expires_in * 1000,
    };
    await this.vaultOperation(async () => {
      if (generation !== this.generation) throw new Error("登录状态已变化。");
      await SecureStore.setItemAsync(SESSION_KEY, JSON.stringify(next), {
        keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
      });
      if (generation === this.generation) {
        this.session = next;
        this.notify();
      }
    });
  }

  async login(
    username: string,
    password: string,
    organization_id: string,
  ): Promise<void> {
    if (Platform.OS === "web" && !desktopSimulation)
      throw new Error("真实登录需要在 iOS 或 Android App 中进行。");
    const generation = ++this.generation;
    this.abortUploads();
    this.session = null;
    this.notify();
    await this.vaultOperation(() => SecureStore.deleteItemAsync(SESSION_KEY));
    const tokens: Tokens = await rawRequest(
      "/api/v1/mobile-auth/login",
      "POST",
      { username, password, organization_id },
    );
    await this.save(tokens, generation);
  }

  private async refresh(): Promise<void> {
    if (this.refreshing) return this.refreshing;
    const current = this.session;
    const generation = this.generation;
    if (!current) throw new ApiError(401, "请重新登录。");
    this.refreshing = (async () => {
      try {
        const tokens: Tokens = await rawRequest(
          "/api/v1/mobile-auth/refresh",
          "POST",
          { refresh_token: current.refresh_token },
        );
        if (generation !== this.generation) throw new Error("登录状态已变化。");
        if (
          tokens.subject !== current.subject ||
          tokens.organization_id !== current.organization_id
        )
          throw new Error("账户范围已变化，请重新登录。");
        await this.save(tokens, generation);
      } catch (error) {
        // 轮换结果不确定时不能重放旧刷新凭证；409 表示服务端正在刷新，可保留会话重试。
        if (
          !(error instanceof ApiError && error.status === 409) &&
          generation === this.generation
        ) {
          this.session = null;
          this.notify();
          await this.vaultOperation(async () => {
            if (generation === this.generation)
              await SecureStore.deleteItemAsync(SESSION_KEY);
          });
        }
        throw error;
      }
    })().finally(() => {
      this.refreshing = null;
    });
    return this.refreshing;
  }

  private async access(): Promise<string> {
    if (!this.session) throw new ApiError(401, "请重新登录。");
    if (this.session.access_expires_at <= Date.now() + 30000)
      await this.refresh();
    if (!this.session) throw new ApiError(401, "请重新登录。");
    return this.session.access_token;
  }

  async request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
    const generation = this.generation;
    let token = await this.access();
    try {
      return (await rawRequest(path, method, body, token)) as T;
    } catch (error) {
      if (
        !(error instanceof ApiError && error.status === 401) ||
        generation !== this.generation
      )
        throw error;
      if (token === this.session?.access_token) await this.refresh();
      token = await this.access();
      return (await rawRequest(path, method, body, token)) as T;
    }
  }

  async logout(): Promise<void> {
    let token: string;
    try {
      token = await this.access();
    } catch (error) {
      this.generation++;
      this.session = null;
      this.notify();
      this.abortUploads();
      await this.vaultOperation(() => SecureStore.deleteItemAsync(SESSION_KEY));
      throw error;
    }
    this.generation++;
    this.session = null;
    this.notify();
    this.abortUploads();
    await this.vaultOperation(() => SecureStore.deleteItemAsync(SESSION_KEY));
    try {
      await rawRequest("/api/v1/mobile-auth/logout", "POST", undefined, token);
    } catch {
      throw new Error(
        "本机已退出，但服务端撤销未确认；请恢复网络后联系管理员。",
      );
    }
  }

  async conversationsPage(before?: string): Promise<{ conversations: RemoteConversation[]; next_cursor: string | null }> {
    return this.request(`${AGENT}/conversations${before ? `?before=${encodeURIComponent(before)}` : ""}`);
  }
  async createConversation(): Promise<string> {
    return (
      await this.request<{ conversation_id: string }>(
        AGENT + "/conversations",
        "POST",
      )
    ).conversation_id;
  }
  async legacyConversations(): Promise<RemoteLegacyConversation[]> {
    return (await this.request<{conversations: RemoteLegacyConversation[]}>(AGENT + "/conversations/legacy-available")).conversations;
  }
  async ownerRecoveryEnabled(): Promise<boolean> {
    return (await this.request<{ owner_recovery_enabled?: boolean }>(AGENT + "/capabilities")).owner_recovery_enabled === true;
  }
  async knowledgeAnalysisEnabled(): Promise<boolean> {
    return (await this.request<{ knowledge_analysis_enabled?: boolean }>(AGENT + "/capabilities")).knowledge_analysis_enabled === true;
  }
  async recoverableConversations(before?: string): Promise<{ conversations: RecoverableConversation[]; next_cursor: string | null }> {
    return this.request(`${AGENT}/conversations/recoverable${before ? `?before=${encodeURIComponent(before)}` : ""}`);
  }
  async recoverConversation(id: string): Promise<void> {
    await this.request(`${AGENT}/conversations/${encodeURIComponent(id)}/recover`, "POST");
  }
  async linkLegacyConversation(threadId: string): Promise<string> {
    return (await this.request<{conversation_id: string}>(AGENT + "/conversations/legacy-link", "POST", {thread_id: threadId})).conversation_id;
  }
  async unlinkLegacyConversation(id: string): Promise<void> {
    await this.request(`${AGENT}/conversations/${encodeURIComponent(id)}/legacy-link`, "DELETE");
  }
  async deleteConversation(id: string): Promise<void> {
    await this.request(
      `${AGENT}/conversations/${encodeURIComponent(id)}`,
      "DELETE",
    );
  }
  async messagesPage(id: string, before?: string, runId?: string): Promise<{ messages: RemoteMessage[]; next_cursor: string | null }> {
    const query = before ? `?before=${encodeURIComponent(before)}` : runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    return this.request(`${AGENT}/conversations/${encodeURIComponent(id)}/messages${query}`);
  }
  async messages(id: string, runId?: string): Promise<RemoteMessage[]> {
    return (await this.messagesPage(id, undefined, runId)).messages;
  }
  async run(id: string): Promise<RemoteRun> {
    return this.request(`${AGENT}/runs/${encodeURIComponent(id)}`);
  }
  async cancel(id: string, cancelRequestId: string): Promise<void> {
    await this.request(
      `${AGENT}/runs/${encodeURIComponent(id)}/cancel`,
      "POST",
      { cancel_request_id: cancelRequestId },
    );
  }
  async removeMedia(id: string): Promise<string> {
    return (await this.request<{ deletion_id: string }>(`${AGENT}/media/${encodeURIComponent(id)}`, "DELETE")).deletion_id;
  }
  async deletionStatus(id: string): Promise<{ status: string; cleared_once: boolean }> {
    return this.request(`${AGENT}/deletions/${encodeURIComponent(id)}`);
  }
  async preview(id: string): Promise<{ uri: string; headers?: Record<string, string>; disposable?: boolean }> {
    const generation = this.generation;
    const path = `${AGENT}/media/${encodeURIComponent(id)}/content`;
    const result = await this.request<{ url: string; mode?: string }>(`${AGENT}/media/${encodeURIComponent(id)}/preview`, "POST");
    if (result.mode !== "gateway") return { uri: result.url };
    if (result.url !== path) throw new Error("素材预览地址无效。");
    const token = await this.access();
    if (generation !== this.generation) throw new Error("账户已变化，请重新登录。");
    if (desktopSimulation) {
      const response = await fetch(baseUrl() + path, { headers: { Authorization: `Bearer ${token}` }, credentials: "omit", cache: "no-store" });
      if (!response.ok) throw new Error("素材暂时无法查看。");
      const blob = await response.blob();
      if (generation !== this.generation) throw new Error("账户已变化，请重新登录。");
      return { uri: URL.createObjectURL(blob), disposable: true };
    }
    return { uri: baseUrl() + path, headers: { Authorization: `Bearer ${token}` } };
  }

  abortUploads(): void {
    this.uploads.forEach((xhr) => xhr.abort());
    this.nativeUploads.forEach((task) => task.cancel());
  }

  private async gatewayTransfer(item: MediaItem, uploadId: string, onProgress: (value: number) => void): Promise<void> {
    const generation = this.generation;
    const token = await this.access();
    if (generation !== this.generation) throw new Error("账户已变化，请重新选择素材。");
    const target = `${baseUrl()}${AGENT}/media/uploads/${encodeURIComponent(uploadId)}/content`;
    if (desktopSimulation) {
      const blob = await (await fetch(item.uri)).blob();
      if (blob.size !== item.size) throw new Error("素材已发生变化，请重新选择。");
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        this.uploads.add(xhr);
        xhr.open("PUT", target);
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        xhr.setRequestHeader("Content-Type", item.mimeType!);
        xhr.timeout = 240000;
        xhr.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(event.loaded / event.total); };
        xhr.onload = () => {
          this.uploads.delete(xhr);
          if (xhr.status >= 200 && xhr.status < 300) resolve();
          else {
            let detail = "上传未完成，请检查状态后重新选择素材。";
            try { const data = JSON.parse(xhr.responseText); if (typeof data.detail === "string" && data.detail.length <= 200) detail = data.detail; } catch { /* no structured error */ }
            reject(new ApiError(xhr.status, detail));
          }
        };
        xhr.onerror = xhr.ontimeout = xhr.onabort = () => {
          this.uploads.delete(xhr);
          reject(new Error("上传结果未确认，请检查状态；如需重传请新建素材。"));
        };
        xhr.send(blob);
      });
      return;
    }
    const task = new File(item.uri).createUploadTask(target, {
      httpMethod: "PUT", uploadType: UploadType.BINARY_CONTENT, mimeType: item.mimeType,
      headers: { Authorization: `Bearer ${token}` }, sessionType: "foreground",
      onProgress: ({ bytesSent, totalBytes }) => { if (totalBytes > 0) onProgress(bytesSent / totalBytes); },
    });
    this.nativeUploads.add(task);
    try {
      const result = await task.uploadAsync();
      if (result.status < 200 || result.status >= 300) {
        let detail = "上传未完成，请检查状态后重新选择素材。";
        try { const data = JSON.parse(result.body); if (typeof data.detail === "string" && data.detail.length <= 200) detail = data.detail; } catch { /* no structured error */ }
        throw new ApiError(result.status, detail);
      }
    } finally {
      this.nativeUploads.delete(task);
      task.release();
    }
  }

  async upload(
    item: MediaItem,
    conversationId: string,
    idempotencyKey: string,
    onProgress: (value: number) => void,
    onRegistered: (uploadId: string, assetId: string) => Promise<void>,
    resume = false,
  ): Promise<string> {
    const generation = this.generation;
    if (!item.size || !item.mimeType)
      throw new Error("无法读取素材类型或大小，请重新选择文件。");
    if (!desktopSimulation) {
      const info = new File(item.uri).info();
      if (!info.exists || info.size !== item.size)
        throw new Error("本机素材已失效或发生变化，请重新选择。");
    }
    const session = await this.request<UploadSession>(
      AGENT + "/media/uploads",
      "POST",
      {
        conversation_id: conversationId,
        idempotency_key: idempotencyKey,
        content_type: item.mimeType,
        size: item.size,
      },
    );
    if (generation !== this.generation)
      throw new Error("账户已变化，请重新选择素材。");
    await onRegistered(session.upload_id, session.asset_id);
    if (generation !== this.generation)
      throw new Error("账户已变化，请重新选择素材。");
    if (session.status === "READY") return session.asset_id;
    if (resume || session.status === "VERIFYING") {
      try {
        const previous = await this.request<{
          status: string;
          asset_id: string | null;
        }>(`${AGENT}/media/uploads/${session.upload_id}/complete`, "POST");
        if (previous.status === "READY" && previous.asset_id)
          return previous.asset_id;
        throw new Error("素材仍在准备，请稍后重试。");
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 404)) throw error;
      }
    }
    if (session.upload_mode === "gateway") {
      try {
        await this.gatewayTransfer(item, session.upload_id, onProgress);
      } catch (error) {
        try {
          if (generation !== this.generation) throw error;
          const recovered = await this.request<{ status: string; asset_id: string | null }>(`${AGENT}/media/uploads/${session.upload_id}/complete`, "POST");
          if (recovered.status === "READY" && recovered.asset_id) return recovered.asset_id;
        } catch { /* one-shot transfer is never automatically replayed */ }
        throw error;
      }
      const done = await this.request<{ status: string; asset_id: string | null }>(`${AGENT}/media/uploads/${session.upload_id}/complete`, "POST");
      if (done.status !== "READY" || !done.asset_id) throw new Error("素材仍在准备，请稍后重试。");
      return done.asset_id;
    }
    if (!session.upload) throw new Error("上传任务正在处理，请稍后重试。");
    const policy = session.upload;
    const target = new URL(policy.url);
    if (
      !(desktopSimulation && target.href === "http://127.0.0.1:8099/mock-oss") && (target.protocol !== "https:" ||
      !/^[a-z0-9][a-z0-9-]+\.oss-[a-z0-9-]+\.aliyuncs\.com$/.test(
        target.hostname,
      ) ||
      target.port ||
      target.username ||
      target.password ||
      target.pathname !== "/" ||
      target.search ||
      target.hash)
    )
      throw new Error("素材上传地址无效。");
    try {
      const webBlob = desktopSimulation ? await (await fetch(item.uri)).blob() : null;
      if (webBlob && webBlob.size !== item.size) throw new Error("素材已发生变化，请重新选择。");
      await new Promise<void>((resolve, reject) => {
        const form = new FormData();
        Object.entries(policy.form_data).forEach(([key, value]) =>
          form.append(key, value),
        );
        if (webBlob) form.append(policy.file_field_name, webBlob, item.name);
        else form.append(policy.file_field_name, {
          uri: item.uri, type: item.mimeType, name: item.name,
        } as unknown as Blob);
        const xhr = new XMLHttpRequest();
        xhr.withCredentials = false;
        this.uploads.add(xhr);
        xhr.open("POST", policy.url);
        xhr.timeout = 180000;
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) onProgress(event.loaded / event.total);
        };
        xhr.onload = () => {
          this.uploads.delete(xhr);
          if (xhr.status === 201) resolve();
          else reject(new Error("上传未完成，请重试。"));
        };
        xhr.onerror = () => {
          this.uploads.delete(xhr);
          reject(new Error("上传状态未确认，请检查网络后重试。"));
        };
        xhr.onabort = () => {
          this.uploads.delete(xhr);
          reject(new Error("上传已停止。"));
        };
        xhr.ontimeout = () => {
          this.uploads.delete(xhr);
          reject(new Error("上传状态未确认，请检查网络后重试。"));
        };
        xhr.send(form);
      });
    } catch (error) {
      // 上传响应丢失可能已经写入 OSS；先确认原上传任务，绝不直接再次提交模型。
      try {
        if (generation !== this.generation) throw error;
        const recovered = await this.request<{
          status: string;
          asset_id: string | null;
        }>(`${AGENT}/media/uploads/${session.upload_id}/complete`, "POST");
        if (recovered.status === "READY" && recovered.asset_id)
          return recovered.asset_id;
      } catch {
        /* 保留原上传错误及原幂等键，供用户显式恢复。 */
      }
      throw error;
    }
    if (generation !== this.generation)
      throw new Error("账户已变化，请重新选择素材。");
    const done = await this.request<{
      status: string;
      asset_id: string | null;
    }>(`${AGENT}/media/uploads/${session.upload_id}/complete`, "POST");
    if (done.status !== "READY" || !done.asset_id)
      throw new Error("素材仍在准备，请稍后重试。");
    return done.asset_id;
  }

  async submit(
    conversationId: string,
    runId: string,
    clientMessageId: string,
    question: string,
    mediaIds: string[],
    analysisMode: "movement" | "knowledge" = "movement",
  ): Promise<void> {
    const result = await this.request<{ run_id: string; content_deleted?: boolean }>(AGENT + "/runs", "POST", {
      conversation_id: conversationId,
      run_id: runId,
      client_message_id: clientMessageId,
      message: question,
      media_ids: mediaIds,
      ...(analysisMode === "knowledge" ? { analysis_mode: analysisMode } : {}),
    });
    if (result.content_deleted || result.run_id !== runId) throw new Error("提交状态与原任务不一致，请刷新对话。");
  }
}

export const mobileApi = new MobileApi();
