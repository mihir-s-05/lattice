// Core types for LATTICE WebUI

export type RunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'queued'

export interface Run {
  run_id: string
  status: RunStatus
  started_at: string
  provider: string
  model: string
  prompt?: string
}

export interface Agent {
  id: string
  name: string
  status: 'idle' | 'planning' | 'acting' | 'reporting'
  current_task?: string
}

export interface ChatMessage {
  id: string
  role: 'router_llm' | 'system' | 'user'
  content: string
  timestamp: string
  annotations?: Record<string, any>
}

export interface SystemEvent {
  id: string
  type: 'plan_switch' | 'provider_switch' | 'gate_eval' | 'contract_test_result' | 'finalization'
  timestamp: string
  data: Record<string, any>
}

export interface AgentTurn {
  id: string
  agent: string
  phase: 'plan' | 'act' | 'report'
  content: string
  timestamp: string
  artifacts_written: string[]
  rag_queries: string[]
  tool_calls: ToolCall[]
}

export interface ToolCall {
  id: string
  name: string
  arguments: Record<string, any>
  result?: any
}

export interface Huddle {
  id: string
  topic: string
  attendees: string[]
  status: 'active' | 'completed'
  transcript_path?: string
  started_at: string
  completed_at?: string
}

export interface DecisionSummary {
  id: string
  topic: string
  decision: string
  rationale: string
  risks: string[]
  actions: Array<{
    action: string
    owner: string
    deadline?: string
  }>
  contracts: string
  sources: Array<{
    type: 'external' | 'artifact' | 'rag'
    url?: string
    path?: string
    title: string
  }>
  created_at: string
}

export interface WebSearchQuery {
  id: string
  query: string
  source: 'groq' | 'adapter'
  engines: string[]
  time_range?: string
  results_count: number
  urls_fetched: number
  latency_ms: number
  results: WebSearchResult[]
  extracts: WebSearchExtract[]
  timestamp: string
}

export interface WebSearchResult {
  url: string
  title: string
  snippet: string
}

export interface WebSearchExtract {
  url: string
  content: string
  extracted_at: string
}

export interface Gate {
  id: string
  name: string
  status: 'pending' | 'passed' | 'failed'
  conditions: string[]
  checked_conditions: string[]
  evidence: string[]
  last_evaluated?: string
}

export interface ContractTest {
  id: string
  name: string
  status: 'pending' | 'passed' | 'failed'
  metrics: Record<string, any>
  evidence: string[]
  last_run?: string
}

export interface Artifact {
  path: string
  type: 'code' | 'spec' | 'decision' | 'huddle' | 'log' | 'deliverable'
  mime_type: string
  size: number
  hash: string
  tags: string[]
  created_at: string
}

export interface PlanGraphSegment {
  id: string
  mode: string
  status: 'active' | 'completed' | 'failed'
  critical_path: boolean
}

export interface PlanGraph {
  segments: PlanGraphSegment[]
  current_segment: string
  last_switch_reason?: string
}

// WebSocket event types
export interface WebSocketEvent {
  type: string
  timestamp: string
  run_id: string
  data: any
}

export interface RunStatusEvent extends WebSocketEvent {
  type: 'run_status'
  data: {
    run_id: string
    status: RunStatus
    started_at: string
    provider: string
    model: string
  }
}

export interface RouterMessageEvent extends WebSocketEvent {
  type: 'router_message'
  data: {
    message_id: string
    role: 'router_llm' | 'system'
    content_md: string
    annotations?: Record<string, any>
  }
}

export interface AgentTurnEvent extends WebSocketEvent {
  type: 'agent_turn'
  data: AgentTurn
}

export interface HuddleEvent extends WebSocketEvent {
  type: 'huddle_open' | 'huddle_complete'
  data: {
    huddle_id: string
    topic: string
    attendees: string[]
    transcript_path?: string
  }
}

export interface DecisionSummaryEvent extends WebSocketEvent {
  type: 'decision_summary_created'
  data: {
    ds_id: string
    summary: DecisionSummary
  }
}

export interface WebSearchEvent extends WebSocketEvent {
  type: 'web_search'
  data: WebSearchQuery
}

export interface GateEvalEvent extends WebSocketEvent {
  type: 'gate_eval'
  data: {
    gate_id: string
    status: 'passed' | 'failed'
    checked_conditions: string[]
    evidence: string[]
  }
}

export interface ContractTestEvent extends WebSocketEvent {
  type: 'contract_test_result'
  data: ContractTest
}

export interface PlanSwitchEvent extends WebSocketEvent {
  type: 'plan_switch'
  data: {
    from_mode: string
    to_mode: string
    reason_type: string
    details: string
  }
}

// UI State types
export interface UIFilters {
  timeWindow?: [string, string]
  agents: string[]
  eventTypes: string[]
  status: RunStatus[]
}

export interface UIPreferences {
  theme: 'light' | 'dark' | 'system'
  sidebarCollapsed: boolean
  inspectorCollapsed: boolean
  activeView: 'chat' | 'swimlanes'
  panelStates: {
    huddles: boolean
    decisions: boolean
    webSearch: boolean
    gates: boolean
    tests: boolean
    artifacts: boolean
    planGraph: boolean
  }
}
