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

export interface NormalizedLogEntry {
  timestamp: string;
  level: string;
  source: string;
  message: string;
  metadata: Record<string, unknown>;
  raw: string;
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
