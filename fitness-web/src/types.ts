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
  intent?: string | null;
  routes?: string[];
  route_source?: string | null;
  route_confidence?: number | null;
  request_usage?: {
    model_calls: number;
    usage_complete: boolean;
    input_tokens: number | null;
    output_tokens: number | null;
    estimated_cost: string | null;
    currency: string | null;
  };
};

export type ChatRun = {
  run_id: string;
  status: "RUNNING" | "COMPLETED" | "FAILED" | "UNKNOWN";
  response: ChatResponse | null;
  error_code: string | null;
};

export type ChatStreamEvent = {
  run_id: string;
  sequence: number;
  event: string;
  data: { text?: string; stage?: string; response?: ChatResponse; error_code?: string };
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

export type MemoryCandidate = {
  id: string;
  memory_type: string;
  memory_key: string;
  value: string;
  unit: string | null;
  status: string;
  expires_at: string;
};

export type MemoryCandidateInbox = {
  organization_id: string;
  items: Array<{
    candidate: MemoryCandidate;
    notification_id: string | null;
    notification_status: string | null;
    notification_created_at: string | null;
  }>;
};

export type FitnessMemory = {
  id: string;
  organization_id: string;
  memory_type: string;
  memory_key: string;
  content: Record<string, unknown>;
  status: string;
  version: number;
  created_at: string;
  expires_at: string | null;
  updated_at: string;
  content_redacted: boolean;
};

export type InAppNotification = {
  id: string;
  notification_type: string;
  aggregate_type: string;
  aggregate_id: string;
  organization_id: string;
  template_version: number;
  title: string;
  body: string;
  status: string;
  created_at: string;
  read_at: string | null;
  proactive_candidate_id: string | null;
  cta_type: string | null;
  cta_payload: Record<string, unknown> | null;
};

export type NotificationPreference = {
  organization_id: string;
  notification_type: string;
  enabled: boolean;
  quiet_start: string | null;
  quiet_end: string | null;
  timezone: string;
  minimum_interval_seconds: number;
  updated_at: string | null;
};

export type OperationsMetric = {
  id: string;
  label: string;
  description: string;
  dimension_description: string;
  supported_buckets: string[];
  supports_previous_period: boolean;
  supports_year_over_year: boolean;
};

export type OperationsMetricCatalog = {
  catalog_version: string;
  items: OperationsMetric[];
};

export type OperationsAudit = {
  id: string;
  metric: string;
  metric_definition: OperationsMetric;
  bucket: string;
  comparison_role: string;
  row_count: number | null;
  status: string;
  error_code: string | null;
  created_at: string;
};

export type OperationsAuditPage = {
  items: OperationsAudit[];
  limit: number;
  offset: number;
  has_more: boolean;
};

export type KnowledgeJob = {
  id: string;
  source_uri: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  title: string;
  document_type: string;
  organization_id: string | null;
  visibility: string;
  allowed_roles: string[];
  requested_version: number;
  status: string;
  attempt_count: number;
  max_attempts: number;
  reviewer_id: string | null;
  review_comment: string | null;
  error_message: string | null;
  content_sha256: string;
  safety_status: string;
  malware_status: string;
  scanner_name: string;
  malware_scanner: string;
  malware_signature: string | null;
  malware_scanned_at: string | null;
  error_code: string | null;
  document_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  reviewed_at: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type PdfPageProfile = {
  page_number: number;
  image_count: number;
  image_area_ratio: number;
  native_text_chars: number;
  text_area_ratio: number;
  table_count: number;
  caption_count: number;
  route: string;
  reasons: string[];
};

export type ReviewFinding = {
  code: string;
  severity: string;
  message: string;
  pages: number[];
};

export type KnowledgeReviewReport = {
  id: string;
  job_id: string;
  report_version: number;
  document_sha256: string;
  parser_name: string;
  parser_version: string;
  parser_pipeline_version: string;
  review_policy_version: string;
  media_type: string;
  declared_risk_level: string;
  source_requires_human_review: boolean;
  status: string;
  can_admin_approve: boolean;
  quality_metrics: Record<string, number | string | boolean | null>;
  page_profiles: PdfPageProfile[];
  warnings: string[];
  findings: ReviewFinding[];
  required_review_domains: string[];
  recommended_reviewer_roles: string[];
  required_qualifications: string[];
  created_at: string | null;
};

export type ReindexJob = {
  id: string;
  requested_by: string;
  organization_id: string | null;
  target_document_id: string | null;
  status: string;
  total_documents: number;
  processed_documents: number;
  succeeded_documents: number;
  skipped_documents: number;
  failed_documents: number;
  attempt_count: number;
  max_attempts: number;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type NotificationDeliveryAttempt = {
  id: number;
  outbox_id: string;
  notification_type: string;
  organization_id: string;
  channel: string;
  attempt_no: number;
  status: string;
  error_code: string | null;
  provider_message_id: string | null;
  started_at: string;
  finished_at: string | null;
};

export type BusinessObjective = {
  id: string;
  organization_id: string;
  objective_key: string;
  name: string;
  metric: string;
  direction: "INCREASE" | "DECREASE" | "TARGET";
  target_value: number | null;
  status: string;
  version: number;
};

export type StrategyPolicy = {
  id: string;
  organization_id: string;
  objective_id: string;
  objective_name: string;
  objective_metric: string;
  strategy_key: string;
  version: number;
  mode: "DISABLED" | "REPLAY" | "SHADOW" | "ADVISORY" | "AUTONOMOUS";
  risk_level: "L0" | "L1" | "L2" | "L3" | "L4";
  allowed_actions: string[];
  status: string;
};

export type DecisionOption = {
  id: string;
  case_id: string;
  option_key: string;
  title: string;
  action_type: string;
  expected_benefit: number;
  estimated_cost: number;
  risk_penalty: number;
  uncertainty_penalty: number;
  score: number;
  confidence: number;
};

export type DecisionCase = {
  id: string;
  organization_id: string;
  objective_id: string;
  policy_id: string;
  anomaly_id: string;
  title: string;
  status: string;
  risk_level: string;
  diagnosis: Record<string, unknown>;
  selected_option_id: string | null;
  trace_id: string | null;
  expires_at: string;
  created_at: string;
  updated_at: string;
  options?: DecisionOption[];
  events?: DecisionEvent[];
  actions?: ActionRun[];
};

export type DecisionEvent = {
  id: number;
  event_type: string;
  from_status: string | null;
  to_status: string;
  actor_id: string;
  reason_code: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type DecisionEffect = {
  metric: string;
  window_start: string;
  window_end: string;
  treatment_value: number;
  treatment_samples: number;
  control_value: number;
  control_samples: number;
  absolute_effect: number;
  relative_effect_percent: number | null;
};

export type DecisionExperiment = {
  id: string;
  organization_id: string;
  policy_id: string;
  name: string;
  status: "DRAFT" | "RUNNING" | "STOPPED" | "COMPLETED";
  treatment_percent: number;
  primary_metric: string;
  guardrail_metrics: string[];
  observation_days: number;
  created_at: string;
};

export type ActionRun = {
  id: string;
  case_id: string;
  option_id: string;
  organization_id: string;
  action_type: string;
  status: string;
  attempt_count: number;
  last_error_code: string | null;
  result: Record<string, unknown>;
  created_at: string;
};
