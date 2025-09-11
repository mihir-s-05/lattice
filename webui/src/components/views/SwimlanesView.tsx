import React from 'react'
import { FixedSizeList as List } from 'react-window'
import AutoSizer from 'react-virtualized-auto-sizer'
import { AgentTimeline } from './AgentTimeline'
import { useAppStore } from '@/store'
import type { Agent } from '@/types'
import classNames from 'classnames'

interface SwimlanesViewProps {
  runId: string
}

export function SwimlanesView({ runId }: SwimlanesViewProps) {
  const { runStates } = useAppStore()
  const runState = runStates[runId]

  if (!runState) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center text-gray-500 dark:text-gray-400">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600 mx-auto mb-4"></div>
          <p>Loading agents...</p>
        </div>
      </div>
    )
  }

  const agents = Object.values(runState.agents)

  if (agents.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center h-full">
        <div className="text-center text-gray-500 dark:text-gray-400">
          <div className="text-6xl mb-4">🏊‍♂️</div>
          <h3 className="text-lg font-medium mb-2">No agents active yet</h3>
          <p className="text-sm">
            Agent swimlanes will appear here when agents start working
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 bg-white dark:bg-gray-900">
      {/* Swimlanes Header */}
      <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-medium text-gray-900 dark:text-gray-100">
            Agent Swimlanes
          </h2>
          <div className="text-sm text-gray-500 dark:text-gray-400">
            {agents.length} active agent{agents.length !== 1 ? 's' : ''}
          </div>
        </div>
      </div>

      {/* Swimlanes Grid */}
      <div className="flex-1 overflow-hidden">
        <div className="h-full grid" style={{ gridTemplateColumns: `repeat(${Math.min(agents.length, 4)}, 1fr)` }}>
          {agents.slice(0, 4).map((agent) => (
            <AgentSwimlane
              key={agent.id}
              agent={agent}
              runId={runId}
            />
          ))}
        </div>
        
        {/* Show additional agents if more than 4 */}
        {agents.length > 4 && (
          <div className="p-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800">
            <div className="text-sm text-gray-600 dark:text-gray-400">
              Showing 4 of {agents.length} agents. Additional agents: {agents.slice(4).map(a => a.name).join(', ')}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

interface AgentSwimlaneProps {
  agent: Agent
  runId: string
}

function AgentSwimlane({ agent, runId }: AgentSwimlaneProps) {
  const { runStates } = useAppStore()
  const runState = runStates[runId]

  // Get agent-specific turns
  const agentTurns = runState?.agentTurns.filter(turn => turn.agent === agent.id) || []

  const getStatusColor = (status: Agent['status']) => {
    switch (status) {
      case 'planning':
        return 'text-blue-600 dark:text-blue-400 bg-blue-100 dark:bg-blue-900/20'
      case 'acting':
        return 'text-green-600 dark:text-green-400 bg-green-100 dark:bg-green-900/20'
      case 'reporting':
        return 'text-purple-600 dark:text-purple-400 bg-purple-100 dark:bg-purple-900/20'
      case 'idle':
      default:
        return 'text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-gray-900/20'
    }
  }

  return (
    <div className="border-r border-gray-200 dark:border-gray-700 last:border-r-0 flex flex-col h-full">
      {/* Agent Header */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800">
        <div className="flex items-center justify-between mb-2">
          <h3 className="font-medium text-gray-900 dark:text-gray-100 truncate">
            {agent.name}
          </h3>
          <div className={classNames('px-2 py-1 rounded-full text-xs font-medium', getStatusColor(agent.status))}>
            {agent.status}
          </div>
        </div>
        {agent.current_task && (
          <p className="text-xs text-gray-600 dark:text-gray-400 truncate">
            {agent.current_task}
          </p>
        )}
        <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
          {agentTurns.length} turn{agentTurns.length !== 1 ? 's' : ''}
        </div>
      </div>

      {/* Agent Timeline */}
      <div className="flex-1 min-h-0">
        <AgentTimeline
          agentId={agent.id}
          runId={runId}
          turns={agentTurns}
        />
      </div>
    </div>
  )
}
