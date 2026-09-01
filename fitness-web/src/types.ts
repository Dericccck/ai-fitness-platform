export type Capability = {
  id: string;
  display_name: string;
  domain: string;
  description: string;
  allowed_roles: string[];
  read_only: boolean;
  requires_confirmation: boolean;
  confirmation_action: string | null;
};

export type CapabilityCatalog = {
  catalog_version: string;
  roles: string[];
  items: Capability[];
};

export type ConfirmationSummary = {
  action?: string;
  resource_type?: string;
  resource_id?: string | null;
  [key: string]: unknown;
};

export type ChatResponse = {
  conversation_id: string;
  answer: string;
  route: string;
  tool_steps: number;
  input_tokens: number | null;
  output_tokens: number | null;
  status?: "COMPLETED" | "CONFIRMATION_REQUIRED";
  confirmation_id?: string | null;
  confirmation_summary?: ConfirmationSummary | null;
  confirmation_expires_at?: string | null;
};

export type Confirmation = {
  id: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  display_summary: Record<string, unknown>;
  authorization_status: string;
  execution_status: string;
  version: number;
  expires_at: string;
  last_error_code: string | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  route?: string;
  confirmation?: ChatResponse;
};
