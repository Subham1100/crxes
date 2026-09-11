/**
 * Shared contract with the FastAPI backend (backend/db/models.py).
 * Hand-maintained — update both sides together.
 */

export type Plan = "free" | "pro";

export type Provider =
  | "datadog"
  | "cloudwatch"
  | "gcp"
  | "sentry"
  | "loki"
  | "webhook"
  | "manual";

export type Schedule = "manual" | "15min" | "30min" | "1hr" | "4hr";

export type SourceStatus = "active" | "paused" | "error";

export type AnalysisStatus = "pending" | "running" | "done" | "failed";

export type Severity = "critical" | "high" | "medium" | "low";

export interface User {
  id: string;
  email: string;
  name: string | null;
  avatar_url: string | null;
  plan: Plan;
  created_at: string;
}

export interface Source {
  id: string;
  provider: Provider;
  name: string;
  config: Record<string, unknown>;
  webhook_token: string | null;
  schedule: Schedule;
  auto_analyze: boolean;
  last_pulled_at: string | null;
  status: SourceStatus;
  error_message: string | null;
  created_at: string;
}

/** Canonical severities, least → most severe (backend/ingest/entry.py). */
export type LogLevel = "trace" | "debug" | "info" | "warn" | "error" | "critical" | "fatal";

/** Where a service sits in a request's path, shallow → deep. */
export type ServiceRole =
  | "frontend"
  | "proxy"
  | "gateway"
  | "backend"
  | "worker"
  | "queue"
  | "cache"
  | "db"
  | "external"
  | "unknown";

/** A log format the ingest phase has a parser for. */
export type LogFormat =
  | "plain"
  | "json"
  | "logfmt"
  | "clf"
  | "syslog"
  | "syslog5424"
  | "postgres"
  | "cri";

/**
 * How an entry's timestamp was arrived at. Worth surfacing: an "assumed" or
 * "carried" time is not a measurement, and a timeline built from them can
 * mislead.
 */
export type TimeSource = "offset" | "zone" | "assumed" | "carried" | "none";

/** One log event — not one line; a stack trace folds into its entry. */
export interface NormalizedLogEntry {
  file_id: string;
  line_no: number;
  service: string;
  role: ServiceRole;
  /** ISO-8601 UTC, or "" when the entry has no usable timestamp. */
  timestamp: string;
  time_source: TimeSource;
  level: LogLevel;
  message: string;
  /** Real trace context, only ever read from the log itself. */
  trace_id: string | null;
  span_id: string | null;
  parent_span_id: string | null;
  /** Structured fields the format supplied natively. */
  attributes: Record<string, unknown>;
  /** Explicit join keys — request IDs, PIDs, session IDs. */
  correlation_keys: Record<string, string>;
  raw: string;
  /** Set by the enrich phase (Drain3 template mining). */
  template_id: string | null;
  /** Set by correlation. Inferred grouping — never a real trace ID. */
  flow_id: string | null;
}

/** One file in an ingest request. */
export interface IngestFileIn {
  name: string;
  content: string;
  /** Naming the service turns off per-line service inference for this file. */
  service?: string | null;
  role?: ServiceRole;
  /** Overrides detection. Omit to sniff it. */
  format?: LogFormat | null;
  /** IANA zone, for timestamps that carry no offset. */
  timezone?: string | null;
  offset_seconds?: number;
}

export interface RedactionOptions {
  enabled?: boolean;
  /** Off by default — an IP is a correlation key more often than it is PII. */
  redact_ips?: boolean;
  redact_cards?: boolean;
  disabled_rules?: string[];
}

/** What ingest made of one file — the per-file row in the upload UI. */
export interface IngestFileReport {
  file_id: string;
  name: string;
  service: string;
  role: ServiceRole;
  format: LogFormat;
  /** 0–1. Show it: detection is a guess, and the user can override. */
  format_confidence: number;
  /** For format "json", which producer wrote it. */
  flavor: string | null;
  format_overridden: boolean;
  entry_count: number;
  line_count: number;
  bytes: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  unresolved_timestamps: number;
  applied_offset_seconds: number;
  /** Set when this file's window sits clear of every other file's. */
  skew_warning: string | null;
  dropped_lines: number;
  with_trace_id: number;
  with_correlation_key: number;
}

export interface RedactionSummary {
  total: number;
  entries_affected: number;
  /** Rule name → count, for the "before you send this" list. */
  counts: Record<string, number>;
}

export interface IngestPreview {
  files: IngestFileReport[];
  entry_count: number;
  dropped_lines: number;
  /** 0–1. At 1 the next phase renders; at 0 every link must be inferred. */
  trace_coverage: number;
  /** 0–1. The fallback signal when trace_coverage is 0. */
  correlation_coverage: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  skew_suspected: boolean;
  redaction: RedactionSummary;
  sample: NormalizedLogEntry[];
}

export interface LogPull {
  id: string;
  source_id: string;
  log_count: number;
  time_range_start: string | null;
  time_range_end: string | null;
  raw_size_bytes: number | null;
  pulled_at: string;
}

export interface Prediction {
  id: string;
  analysis_id: string;
  title: string;
  severity: Severity;
  description: string | null;
  confidence: number | null;
  eta: string | null;
  impact: string | null;
  root_cause: string | null;
  recommended_action: string | null;
  was_accurate: boolean | null;
  feedback_note: string | null;
  created_at: string;
}

export interface Analysis {
  id: string;
  source_id: string | null;
  log_pull_id: string | null;
  status: AnalysisStatus;
  current_agent: number | null;
  log_line_count: number | null;
  total_tokens_used: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  model: string | null;
  /** What the run actually cost in USD; null when the model isn't priced. */
  cost_usd: number | null;
  duration_ms: number | null;
  error_message: string | null;
  created_at: string;
}

export interface AnalysisDetail extends Analysis {
  agent_parser_output: string | null;
  agent_pattern_output: string | null;
  agent_rootcause_output: string | null;
  agent_predictor_output: string | null;
  predictions: Prediction[];
}

/** Cost estimate for a paste, from `POST /api/analyses/estimate`. */

export type ModelProvider = "anthropic" | "openai" | "google" | "meta" | "deepseek";

export interface StageCost {
  key: AgentKey;
  name: string;
  input_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
}

export interface ModelCost {
  id: string;
  provider: ModelProvider;
  provider_label: string;
  label: string;
  input_per_mtok: number;
  output_per_mtok: number;
  input_tokens: number;
  output_tokens: number;
  input_usd: number;
  output_usd: number;
  total_usd: number;
  context_window: number;
  fits_context: boolean;
  /** The model the pipeline is configured to run — the row that's real spend. */
  is_current: boolean;
}

export interface CostEstimate {
  log_line_count: number;
  dropped_lines: number;
  raw_size_bytes: number;
  prompt_chars: number;
  log_tokens: number;
  input_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  total_tokens: number;
  largest_prompt_tokens: number;
  /** "counted" — tokenized by the API; "estimated" — character ratio. */
  token_source: "counted" | "estimated";
  effort: string;
  prices_updated: string;
  stages: StageCost[];
  models: ModelCost[];
}

/** The four pipeline agents, in execution order. */
export const AGENTS = [
  { key: "parser", name: "Log Parser", index: 0 },
  { key: "pattern", name: "Pattern Detector", index: 1 },
  { key: "rootcause", name: "Root Cause Analyzer", index: 2 },
  { key: "predictor", name: "Bug Predictor", index: 3 },
] as const;

export type AgentKey = (typeof AGENTS)[number]["key"];

/** SSE events published on the `analysis:{id}` channel. */
export type AnalysisEvent =
  | { event: "status"; data: { status: AnalysisStatus; current_agent: number } }
  | { event: "agent_start"; data: { agent: AgentKey; index: number; name: string } }
  | { event: "agent_done"; data: { agent: AgentKey; index: number; output: string } }
  | { event: "predictions"; data: Prediction[] }
  | {
      event: "complete";
      data: {
        analysis_id: string;
        duration_ms: number;
        tokens_used: number;
        prediction_count: number;
      };
    }
  | { event: "error"; data: { message: string; agent?: AgentKey } };
