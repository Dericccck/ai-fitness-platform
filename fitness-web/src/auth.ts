export type AuthConfig = { enabled: boolean; demo_identity_allowed: boolean };
export type BrowserSession = {
  authenticated: true; subject: string; organization_id: string; roles: string[];
  expires_at: number; session_expires_at: number; csrf_token: string;
};

export class AuthError extends Error {
  readonly status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

// 注入 fetch，测试无需启动真实登录端；不保存密码/AgentContext/refresh token。
export class AuthClient {
  config: AuthConfig | null = null;
  session: BrowserSession | null = null;
  private configFlight: Promise<AuthConfig> | null = null;
  private sessionFlight: Promise<BrowserSession> | null = null;
  private refreshFlight: Promise<BrowserSession> | null = null;
  private readonly baseUrl: string;
  private readonly fetcher: typeof fetch;
  private readonly scopeChanged: () => void;

  constructor(baseUrl: string, fetcher: typeof fetch, scopeChanged: () => void) {
    this.baseUrl = baseUrl; this.fetcher = fetcher; this.scopeChanged = scopeChanged;
  }

  private async call<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.fetcher(`${this.baseUrl}/api/v1/auth/${path}`, {
      ...init, credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) {
      if (response.status === 401) this.session = null;
      const messages: Record<number, string> = {
        401: "登录或会话已失效，请重新登录", 403: "登录机构或请求来源未获授权",
        409: "会话正在其他窗口刷新，请稍后重试", 429: "登录请求过于频繁，请稍后再试",
        503: "正式登录服务尚未配置或暂时不可用",
      };
      throw new AuthError(messages[response.status] ?? "登录请求失败", response.status);
    }
    return await response.json() as T;
  }

  async configuration(): Promise<AuthConfig> {
    if (this.config) return this.config;
    this.configFlight ??= this.call<AuthConfig>("config");
    try { return this.config = await this.configFlight; }
    finally { this.configFlight = null; }
  }

  private accept(next: BrowserSession): BrowserSession {
    const previous = this.session;
    this.session = next;
    if (previous && JSON.stringify([previous.subject, previous.organization_id, [...previous.roles].sort()]) !==
      JSON.stringify([next.subject, next.organization_id, [...next.roles].sort()])) {
      this.scopeChanged();
      throw new AuthError("账号授权范围已变化，请刷新页面重新载入工作台", 401);
    }
    return next;
  }

  async load(): Promise<BrowserSession> {
    this.sessionFlight ??= this.call<BrowserSession>("session");
    try { return this.accept(await this.sessionFlight); }
    finally { this.sessionFlight = null; }
  }

  async refresh(): Promise<BrowserSession> {
    if (!this.session) await this.load();
    this.refreshFlight ??= this.call<BrowserSession>("refresh", {
      method: "POST", headers: { "X-CSRF-Token": this.session!.csrf_token },
    });
    try { return this.accept(await this.refreshFlight); }
    finally { this.refreshFlight = null; }
  }

  async headers(method: string, demoContext: () => string): Promise<Headers> {
    const config = await this.configuration();
    const headers = new Headers();
    if (!config.enabled) {
      if (!config.demo_identity_allowed) throw new AuthError("正式登录尚未启用，请联系管理员", 503);
      headers.set("X-Agent-Context", demoContext());
      return headers;
    }
    // 每次动作开始前读取服务端会话，发现别的窗口切换账号；不在业务 POST 401 后重发。
    await this.load();
    if (this.session!.expires_at * 1000 <= Date.now() + 60_000) await this.refresh();
    if (!["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) headers.set("X-CSRF-Token", this.session!.csrf_token);
    return headers;
  }

  async login(username: string, password: string, organization: string): Promise<void> {
    const session = await this.call<BrowserSession>("login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, organization_id: organization }),
    });
    this.session = session;
    this.scopeChanged();
  }

  async logout(): Promise<void> {
    if (!this.session) await this.load();
    await this.call("logout", { method: "POST", headers: { "X-CSRF-Token": this.session!.csrf_token } });
    this.session = null;
    this.scopeChanged();
  }
}
