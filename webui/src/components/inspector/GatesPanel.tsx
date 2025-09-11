import React, { useState } from 'react'
import {
  ShieldCheckIcon,
  CheckCircleIcon,
  XCircleIcon,
  ClockIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  DocumentTextIcon,
} from '@heroicons/react/24/outline'
import { useAppStore } from '@/store'
import type { Gate } from '@/types'
import classNames from 'classnames'

export function GatesPanel() {
  const { currentRunId, runStates } = useAppStore()
  
  if (!currentRunId) {
    return <div className="text-sm text-gray-500 dark:text-gray-400">No run selected</div>
  }

  const runState = runStates[currentRunId]
  const gates = runState ? Object.values(runState.gates) : []

  if (gates.length === 0) {
    return (
      <div className="text-center py-6">
        <ShieldCheckIcon className="h-8 w-8 text-gray-400 mx-auto mb-2" />
        <p className="text-sm text-gray-500 dark:text-gray-400">No gates configured</p>
      </div>
    )
  }

  const pendingGates = gates.filter(g => g.status === 'pending')
  const passedGates = gates.filter(g => g.status === 'passed')
  const failedGates = gates.filter(g => g.status === 'failed')

  return (
    <div className="space-y-4">
      {/* Pending Gates */}
      {pendingGates.length > 0 && (
        <GateGroup
          title="Pending"
          gates={pendingGates}
          defaultExpanded={true}
        />
      )}

      {/* Failed Gates */}
      {failedGates.length > 0 && (
        <GateGroup
          title="Failed"
          gates={failedGates}
          defaultExpanded={true}
        />
      )}

      {/* Passed Gates */}
      {passedGates.length > 0 && (
        <GateGroup
          title="Passed"
          gates={passedGates}
          defaultExpanded={false}
        />
      )}
    </div>
  )
}

interface GateGroupProps {
  title: string
  gates: Gate[]
  defaultExpanded: boolean
}

function GateGroup({ title, gates, defaultExpanded }: GateGroupProps) {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded)

  return (
    <div>
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center justify-between w-full text-left focus-ring rounded"
      >
        <h4 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide">
          {title} ({gates.length})
        </h4>
        {isExpanded ? (
          <ChevronDownIcon className="h-4 w-4 text-gray-400" />
        ) : (
          <ChevronRightIcon className="h-4 w-4 text-gray-400" />
        )}
      </button>

      {isExpanded && (
        <div className="mt-2 space-y-2">
          {gates.map((gate) => (
            <GateCard key={gate.id} gate={gate} />
          ))}
        </div>
      )}
    </div>
  )
}

interface GateCardProps {
  gate: Gate
}

function GateCard({ gate }: GateCardProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  const getStatusIcon = () => {
    switch (gate.status) {
      case 'passed':
        return <CheckCircleIcon className="h-5 w-5 text-green-600 dark:text-green-400" />
      case 'failed':
        return <XCircleIcon className="h-5 w-5 text-red-600 dark:text-red-400" />
      case 'pending':
      default:
        return <ClockIcon className="h-5 w-5 text-yellow-600 dark:text-yellow-400" />
    }
  }

  const getStatusColor = () => {
    switch (gate.status) {
      case 'passed':
        return 'border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/10'
      case 'failed':
        return 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/10'
      case 'pending':
      default:
        return 'border-yellow-200 dark:border-yellow-800 bg-yellow-50 dark:bg-yellow-900/10'
    }
  }

  return (
    <div className={classNames('border rounded-lg overflow-hidden', getStatusColor())}>
      {/* Header */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full p-3 text-left hover:bg-black/5 dark:hover:bg-white/5 focus-ring transition-colors"
      >
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-3 flex-1 min-w-0">
            {getStatusIcon()}
            <div className="flex-1 min-w-0">
              <h5 className="font-medium text-sm text-gray-900 dark:text-gray-100 mb-1">
                {gate.name}
              </h5>
              <div className="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400">
                <span className={classNames('badge', `badge-${gate.status}`)}>
                  {gate.status}
                </span>
                <span>{gate.checked_conditions.length}/{gate.conditions.length} conditions</span>
                {gate.last_evaluated && (
                  <span>Last: {new Date(gate.last_evaluated).toLocaleTimeString()}</span>
                )}
              </div>
            </div>
          </div>
          <div className={classNames(
            'transform transition-transform ml-2',
            isExpanded ? 'rotate-180' : ''
          )}>
            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>
      </button>

      {/* Expanded Content */}
      {isExpanded && (
        <div className="border-t border-current/20 p-3 bg-black/5 dark:bg-white/5">
          <div className="space-y-4">
            {/* Conditions */}
            <div>
              <h6 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
                Conditions ({gate.conditions.length})
              </h6>
              <div className="space-y-1">
                {gate.conditions.map((condition, index) => {
                  const isChecked = gate.checked_conditions.includes(condition)
                  return (
                    <div
                      key={index}
                      className={classNames(
                        'flex items-start gap-2 p-2 rounded text-sm',
                        isChecked
                          ? 'bg-green-100 dark:bg-green-900/20 text-green-800 dark:text-green-200'
                          : 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300'
                      )}
                    >
                      {isChecked ? (
                        <CheckCircleIcon className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5" />
                      ) : (
                        <ClockIcon className="h-4 w-4 text-gray-400 mt-0.5" />
                      )}
                      <span>{condition}</span>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Evidence */}
            {gate.evidence.length > 0 && (
              <div>
                <h6 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
                  Evidence ({gate.evidence.length})
                </h6>
                <div className="space-y-1">
                  {gate.evidence.map((evidence, index) => (
                    <div
                      key={index}
                      className="flex items-start gap-2 p-2 rounded bg-blue-50 dark:bg-blue-900/20 text-sm"
                    >
                      <DocumentTextIcon className="h-4 w-4 text-blue-600 dark:text-blue-400 mt-0.5" />
                      <span className="text-blue-800 dark:text-blue-200">{evidence}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-2 pt-2 border-t border-current/20">
              <button className="text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 focus-ring rounded px-2 py-1">
                View Details
              </button>
              {gate.status === 'failed' && (
                <button className="text-xs text-orange-600 hover:text-orange-700 dark:text-orange-400 dark:hover:text-orange-300 focus-ring rounded px-2 py-1">
                  Retry
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
