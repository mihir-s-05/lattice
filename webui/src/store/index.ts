import { create } from 'zustand'
import { devtools } from 'zustand/middleware'
import { immer } from 'zustand/middleware/immer'
import type {
  Run,
  Agent,
  ChatMessage,
  SystemEvent,
  AgentTurn,
  Huddle,
  DecisionSummary,
  WebSearchQuery,
  Gate,
  ContractTest,
  Artifact,
  PlanGraph,
  UIFilters,
  UIPreferences,
  RunStatus,
} from '@/types'

interface RunState {
  messages: ChatMessage[]
  events: SystemEvent[]
  agentTurns: AgentTurn[]
  agents: Record<string, Agent>
  huddles: Record<string, Huddle>
  decisions: Record<string, DecisionSummary>
  webSearchQueries: WebSearchQuery[]
  gates: Record<string, Gate>
  tests: Record<string, ContractTest>
  artifacts: Artifact[]
  planGraph: PlanGraph | null
  lastEventId: string | null
  isConnected: boolean
  isReconnecting: boolean
}

interface AppState {
  // Current run data
  currentRunId: string | null
  runs: Record<string, Run>
  runStates: Record<string, RunState>
  
  // UI state
  preferences: UIPreferences
  filters: UIFilters
  
  // Connection state
  wsConnections: Record<string, WebSocket | null>
  
  // Actions
  setCurrentRun: (runId: string | null) => void
  addRun: (run: Run) => void
  updateRun: (runId: string, updates: Partial<Run>) => void
  
  // Run state actions
  initRunState: (runId: string) => void
  addMessage: (runId: string, message: ChatMessage) => void
  addEvent: (runId: string, event: SystemEvent) => void
  addAgentTurn: (runId: string, turn: AgentTurn) => void
  updateAgent: (runId: string, agentId: string, updates: Partial<Agent>) => void
  addHuddle: (runId: string, huddle: Huddle) => void
  updateHuddle: (runId: string, huddleId: string, updates: Partial<Huddle>) => void
  addDecision: (runId: string, decision: DecisionSummary) => void
  addWebSearch: (runId: string, query: WebSearchQuery) => void
  updateGate: (runId: string, gateId: string, updates: Partial<Gate>) => void
  updateTest: (runId: string, testId: string, updates: Partial<ContractTest>) => void
  setArtifacts: (runId: string, artifacts: Artifact[]) => void
  setPlanGraph: (runId: string, planGraph: PlanGraph) => void
  setConnectionState: (runId: string, isConnected: boolean, isReconnecting?: boolean) => void
  
  // UI actions
  updatePreferences: (updates: Partial<UIPreferences>) => void
  updateFilters: (updates: Partial<UIFilters>) => void
  togglePanel: (panel: keyof UIPreferences['panelStates']) => void
  
  // WebSocket actions
  setWebSocket: (runId: string, ws: WebSocket | null) => void
}

const createInitialRunState = (): RunState => ({
  messages: [],
  events: [],
  agentTurns: [],
  agents: {},
  huddles: {},
  decisions: {},
  webSearchQueries: [],
  gates: {},
  tests: {},
  artifacts: [],
  planGraph: null,
  lastEventId: null,
  isConnected: false,
  isReconnecting: false,
})

const initialPreferences: UIPreferences = {
  theme: 'system',
  sidebarCollapsed: false,
  inspectorCollapsed: false,
  activeView: 'chat',
  panelStates: {
    huddles: true,
    decisions: true,
    webSearch: false,
    gates: true,
    tests: true,
    artifacts: true,
    planGraph: true,
  },
}

const initialFilters: UIFilters = {
  agents: [],
  eventTypes: [],
  status: [],
}

export const useAppStore = create<AppState>()(
  devtools(
    immer((set, get) => ({
      // Initial state
      currentRunId: null,
      runs: {},
      runStates: {},
      preferences: initialPreferences,
      filters: initialFilters,
      wsConnections: {},

      // Run management
      setCurrentRun: (runId) => {
        set((state) => {
          state.currentRunId = runId
          if (runId && !state.runStates[runId]) {
            state.runStates[runId] = createInitialRunState()
          }
        })
      },

      addRun: (run) => {
        set((state) => {
          state.runs[run.run_id] = run
        })
      },

      updateRun: (runId, updates) => {
        set((state) => {
          if (state.runs[runId]) {
            Object.assign(state.runs[runId], updates)
          }
        })
      },

      // Run state management
      initRunState: (runId) => {
        set((state) => {
          if (!state.runStates[runId]) {
            state.runStates[runId] = createInitialRunState()
          }
        })
      },

      addMessage: (runId, message) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState) {
            runState.messages.push(message)
          }
        })
      },

      addEvent: (runId, event) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState) {
            runState.events.push(event)
          }
        })
      },

      addAgentTurn: (runId, turn) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState) {
            runState.agentTurns.push(turn)
            // Update agent status
            if (!runState.agents[turn.agent]) {
              runState.agents[turn.agent] = {
                id: turn.agent,
                name: turn.agent,
                status: 'idle',
              }
            }
            runState.agents[turn.agent].status = turn.phase === 'plan' ? 'planning' : 
              turn.phase === 'act' ? 'acting' : 'reporting'
          }
        })
      },

      updateAgent: (runId, agentId, updates) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState && runState.agents[agentId]) {
            Object.assign(runState.agents[agentId], updates)
          }
        })
      },

      addHuddle: (runId, huddle) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState) {
            runState.huddles[huddle.id] = huddle
          }
        })
      },

      updateHuddle: (runId, huddleId, updates) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState && runState.huddles[huddleId]) {
            Object.assign(runState.huddles[huddleId], updates)
          }
        })
      },

      addDecision: (runId, decision) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState) {
            runState.decisions[decision.id] = decision
          }
        })
      },

      addWebSearch: (runId, query) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState) {
            runState.webSearchQueries.push(query)
          }
        })
      },

      updateGate: (runId, gateId, updates) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState) {
            if (!runState.gates[gateId]) {
              runState.gates[gateId] = {
                id: gateId,
                name: gateId,
                status: 'pending',
                conditions: [],
                checked_conditions: [],
                evidence: [],
              }
            }
            Object.assign(runState.gates[gateId], updates)
          }
        })
      },

      updateTest: (runId, testId, updates) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState) {
            if (!runState.tests[testId]) {
              runState.tests[testId] = {
                id: testId,
                name: testId,
                status: 'pending',
                metrics: {},
                evidence: [],
              }
            }
            Object.assign(runState.tests[testId], updates)
          }
        })
      },

      setArtifacts: (runId, artifacts) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState) {
            runState.artifacts = artifacts
          }
        })
      },

      setPlanGraph: (runId, planGraph) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState) {
            runState.planGraph = planGraph
          }
        })
      },

      setConnectionState: (runId, isConnected, isReconnecting = false) => {
        set((state) => {
          const runState = state.runStates[runId]
          if (runState) {
            runState.isConnected = isConnected
            runState.isReconnecting = isReconnecting
          }
        })
      },

      // UI actions
      updatePreferences: (updates) => {
        set((state) => {
          Object.assign(state.preferences, updates)
        })
      },

      updateFilters: (updates) => {
        set((state) => {
          Object.assign(state.filters, updates)
        })
      },

      togglePanel: (panel) => {
        set((state) => {
          state.preferences.panelStates[panel] = !state.preferences.panelStates[panel]
        })
      },

      // WebSocket actions
      setWebSocket: (runId, ws) => {
        set((state) => {
          state.wsConnections[runId] = ws
        })
      },
    })),
    {
      name: 'lattice-store',
      partialize: (state) => ({
        preferences: state.preferences,
        filters: state.filters,
      }),
    }
  )
)
