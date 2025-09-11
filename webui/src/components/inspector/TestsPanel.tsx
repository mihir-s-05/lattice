import React, { useState } from 'react'
import {
  CheckCircleIcon,
  XCircleIcon,
  ClockIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  DocumentTextIcon,
  ChartBarIcon,
} from '@heroicons/react/24/outline'
import { useAppStore } from '@/store'
import type { ContractTest } from '@/types'
import classNames from 'classnames'

export function TestsPanel() {
  const { currentRunId, runStates } = useAppStore()
  
  if (!currentRunId) {
    return <div className="text-sm text-gray-500 dark:text-gray-400">No run selected</div>
  }

  const runState = runStates[currentRunId]
  const tests = runState ? Object.values(runState.tests) : []

  if (tests.length === 0) {
    return (
      <div className="text-center py-6">
        <CheckCircleIcon className="h-8 w-8 text-gray-400 mx-auto mb-2" />
        <p className="text-sm text-gray-500 dark:text-gray-400">No tests configured</p>
      </div>
    )
  }

  const pendingTests = tests.filter(t => t.status === 'pending')
  const passedTests = tests.filter(t => t.status === 'passed')
  const failedTests = tests.filter(t => t.status === 'failed')

  return (
    <div className="space-y-4">
      {/* Failed Tests */}
      {failedTests.length > 0 && (
        <TestGroup
          title="Failed"
          tests={failedTests}
          defaultExpanded={true}
        />
      )}

      {/* Pending Tests */}
      {pendingTests.length > 0 && (
        <TestGroup
          title="Pending"
          tests={pendingTests}
          defaultExpanded={true}
        />
      )}

      {/* Passed Tests */}
      {passedTests.length > 0 && (
        <TestGroup
          title="Passed"
          tests={passedTests}
          defaultExpanded={false}
        />
      )}
    </div>
  )
}

interface TestGroupProps {
  title: string
  tests: ContractTest[]
  defaultExpanded: boolean
}

function TestGroup({ title, tests, defaultExpanded }: TestGroupProps) {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded)

  return (
    <div>
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center justify-between w-full text-left focus-ring rounded"
      >
        <h4 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide">
          {title} ({tests.length})
        </h4>
        {isExpanded ? (
          <ChevronDownIcon className="h-4 w-4 text-gray-400" />
        ) : (
          <ChevronRightIcon className="h-4 w-4 text-gray-400" />
        )}
      </button>

      {isExpanded && (
        <div className="mt-2 space-y-2">
          {tests.map((test) => (
            <TestCard key={test.id} test={test} />
          ))}
        </div>
      )}
    </div>
  )
}

interface TestCardProps {
  test: ContractTest
}

function TestCard({ test }: TestCardProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  const getStatusIcon = () => {
    switch (test.status) {
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
    switch (test.status) {
      case 'passed':
        return 'border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/10'
      case 'failed':
        return 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/10'
      case 'pending':
      default:
        return 'border-yellow-200 dark:border-yellow-800 bg-yellow-50 dark:bg-yellow-900/10'
    }
  }

  const getStatusBadge = () => {
    switch (test.status) {
      case 'passed':
        return '✓'
      case 'failed':
        return '✗'
      case 'pending':
      default:
        return '⏳'
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
                {test.name}
              </h5>
              <div className="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400">
                <span className="badge badge-test">
                  {getStatusBadge()}
                </span>
                <span>{test.status}</span>
                {test.last_run && (
                  <span>Last run: {new Date(test.last_run).toLocaleTimeString()}</span>
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
            {/* Metrics */}
            {Object.keys(test.metrics).length > 0 && (
              <div>
                <h6 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2 flex items-center gap-1">
                  <ChartBarIcon className="h-3 w-3" />
                  Metrics
                </h6>
                <div className="grid grid-cols-2 gap-3">
                  {Object.entries(test.metrics).map(([key, value]) => (
                    <div
                      key={key}
                      className="p-2 rounded bg-blue-50 dark:bg-blue-900/20"
                    >
                      <div className="text-xs font-medium text-blue-800 dark:text-blue-200 truncate">
                        {key}
                      </div>
                      <div className="text-sm font-mono text-blue-900 dark:text-blue-100">
                        {typeof value === 'number' 
                          ? value.toLocaleString() 
                          : JSON.stringify(value)
                        }
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Evidence */}
            {test.evidence.length > 0 && (
              <div>
                <h6 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
                  Evidence ({test.evidence.length})
                </h6>
                <div className="space-y-1">
                  {test.evidence.map((evidence, index) => (
                    <div
                      key={index}
                      className="flex items-start gap-2 p-2 rounded bg-purple-50 dark:bg-purple-900/20 text-sm"
                    >
                      <DocumentTextIcon className="h-4 w-4 text-purple-600 dark:text-purple-400 mt-0.5" />
                      <span className="text-purple-800 dark:text-purple-200 break-words">
                        {evidence}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Quick Stats */}
            <div className="grid grid-cols-3 gap-2 pt-2 border-t border-current/20">
              <div className="text-center p-2 rounded bg-gray-100 dark:bg-gray-800">
                <div className="text-xs text-gray-500 dark:text-gray-400">Status</div>
                <div className="font-medium text-sm text-gray-900 dark:text-gray-100 capitalize">
                  {test.status}
                </div>
              </div>
              <div className="text-center p-2 rounded bg-gray-100 dark:bg-gray-800">
                <div className="text-xs text-gray-500 dark:text-gray-400">Metrics</div>
                <div className="font-medium text-sm text-gray-900 dark:text-gray-100">
                  {Object.keys(test.metrics).length}
                </div>
              </div>
              <div className="text-center p-2 rounded bg-gray-100 dark:bg-gray-800">
                <div className="text-xs text-gray-500 dark:text-gray-400">Evidence</div>
                <div className="font-medium text-sm text-gray-900 dark:text-gray-100">
                  {test.evidence.length}
                </div>
              </div>
            </div>

            {/* Actions */}
            <div className="flex gap-2 pt-2 border-t border-current/20">
              <button className="text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 focus-ring rounded px-2 py-1">
                View Details
              </button>
              {test.status === 'failed' && (
                <button className="text-xs text-orange-600 hover:text-orange-700 dark:text-orange-400 dark:hover:text-orange-300 focus-ring rounded px-2 py-1">
                  Retry Test
                </button>
              )}
              {test.status === 'pending' && (
                <button className="text-xs text-green-600 hover:text-green-700 dark:text-green-400 dark:hover:text-green-300 focus-ring rounded px-2 py-1">
                  Run Now
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
