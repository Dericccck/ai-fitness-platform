/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AGENT_API_BASE_URL?: string;
  readonly VITE_AGENT_CONTEXT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
