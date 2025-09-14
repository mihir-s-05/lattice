export interface RunMeta {
  run_id: string;
  created_at: string;
  status: 'running' | 'completed' | 'failed';
  summary: string;
  router_provider: string;
  model: string;
}

export interface RunDetails {
  run_id: string;
  status: string;
  agents: string[];
  current_mode: string;
  artifacts_root: string;
  has_web_search: boolean;
}

export interface Annotation {
  type: string;
  ref: string;
}

export interface ChatPayload {
  message_id: string;
  parent_id?: string;
  text: string;
  annotations?: Annotation[];
  is_partial: boolean;
}

export interface ToolPayload {
  tool: string;
  args: Record<string, unknown>;
  result?: Record<string, unknown>;
  status: 'started' | 'succeeded' | 'failed';
}

export interface HuddlePayload {
  event: 'huddle_opened' | 'huddle_closed';
  huddle_id: string;
  topic: string;
  attendees: string[];
  transcript_path?: string;
}

export interface DecisionSummary {
  id: string;
  topic: string;
  decision: string;
  sources?: { type: string; url?: string; id?: string }[];
}

export interface DecisionPayload {
  event: 'decision_recorded';
  decision_id: string;
  summary: DecisionSummary;
}

export interface SearchPayload {
  event: 'web_search';
  source: string;
  query: string;
  results_count: number;
  urls_fetched: number;
}

export interface EventEnvelope {
  ts: string;
  kind: string;
  role: string;
  run_id: string;
  payload: unknown;
}

export interface Message {
  id: string;
  role: string;
  text: string;
  annotations?: Annotation[];
}

export interface Huddle {
  id: string;
  topic: string;
  attendees: string[];
  open: boolean;
}

export interface SearchEvent {
  id: string;
  source: string;
  query: string;
  results_count: number;
}
*** End ***
