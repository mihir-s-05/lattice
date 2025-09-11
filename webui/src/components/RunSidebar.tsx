import React from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  PlayIcon,
  StopIcon,
  CheckCircleIcon,
  XCircleIcon,
  ClockIcon,
  PlusIcon,
} from '@heroicons/react/24/outline'
import { apiClient } from '@/api/client'
import { useAppStore } from '@/store'
import type { RunStatus } from '@/types'
import classNames from 'classnames'

interface RunSidebarProps {
  collapsed: boolean
  searchQuery: string
}

const STATUS_ICONS = {
  running: PlayIcon,
  completed: CheckCircleIcon,
  failed: XCircleIcon,
  pending: ClockIcon,
  queued: ClockIcon,
}

const STATUS_COLORS = {
  running: 'text-blue-600',
  completed: 'text-green-600',
  failed: 'text-red-600',
  pending: 'text-yellow-600',
  queued: 'text-gray-600',
}

export function RunSidebar({ collapsed, searchQuery }: RunSidebarProps) {
  const { runId: currentRunId } = useParams()
  const { runs, addRun } = useAppStore()

  const { data: fetchedRuns = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['runs'],
    // Wrap in function to preserve ApiClient 'this' context
    queryFn: () => apiClient.getRuns(),
    refetchInterval: 5000, // Poll every 5 seconds
    retry: 2,
  })

  // Add fetched runs to store
  React.useEffect(() => {
    fetchedRuns.forEach(addRun)
  }, [fetchedRuns, addRun])

  const runsList = Object.values(runs)
  
  const filteredRuns = runsList.filter(run => {
    if (!searchQuery) return true
    return run.run_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
           run.provider.toLowerCase().includes(searchQuery.toLowerCase()) ||
           run.model.toLowerCase().includes(searchQuery.toLowerCase())
  })

  const groupedRuns = {
    running: filteredRuns.filter(run => run.status === 'running'),
    completed: filteredRuns.filter(run => run.status === 'completed'),
    failed: filteredRuns.filter(run => run.status === 'failed'),
    pending: filteredRuns.filter(run => run.status === 'pending' || run.status === 'queued'),
  }

  if (collapsed) {
    return (
      <div className="p-2 space-y-2">
        <button
          className="w-full p-2 rounded-md bg-primary-600 hover:bg-primary-700 text-white focus-ring"
          title="New Run"
        >
          <PlusIcon className="h-5 w-5 mx-auto" />
        </button>
        
        {runsList.slice(0, 8).map((run) => {
          const StatusIcon = STATUS_ICONS[run.status]
          return (
            <Link
              key={run.run_id}
              to={`/runs/${run.run_id}`}
              className={classNames(
                'block p-2 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 focus-ring',
                currentRunId === run.run_id && 'bg-primary-100 dark:bg-primary-900'
              )}
              title={`${run.run_id} - ${run.status}`}
            >
              <StatusIcon className={classNames('h-5 w-5 mx-auto', STATUS_COLORS[run.status])} />
            </Link>
          )
        })}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Quick Actions */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-700">
        <button className="w-full btn-primary flex items-center gap-2">
          <PlusIcon className="h-4 w-4" />
          New Run
        </button>
      </div>

      {/* Run List */}
      <div className="flex-1 overflow-y-auto sidebar-scroll">
        {isLoading ? (
          <div className="p-4 text-center text-gray-500">Loading runs...</div>
        ) : isError ? (
          <div className="p-4 text-center text-red-600 dark:text-red-400 space-y-2">
            <div>Failed to load runs</div>
            <div className="text-xs opacity-80 truncate max-w-full">{(error as Error)?.message ?? 'Unexpected error'}</div>
            <button onClick={() => refetch()} className="btn-secondary mt-1">Retry</button>
          </div>
        ) : (
          <div className="space-y-4 p-4">
            {/* Active Runs */}
            {groupedRuns.running.length > 0 && (
              <RunGroup
                title="Running"
                runs={groupedRuns.running}
                currentRunId={currentRunId}
              />
            )}

            {/* Pending Runs */}
            {groupedRuns.pending.length > 0 && (
              <RunGroup
                title="Pending"
                runs={groupedRuns.pending}
                currentRunId={currentRunId}
              />
            )}

            {/* Completed Runs */}
            {groupedRuns.completed.length > 0 && (
              <RunGroup
                title="Completed"
                runs={groupedRuns.completed}
                currentRunId={currentRunId}
              />
            )}

            {/* Failed Runs */}
            {groupedRuns.failed.length > 0 && (
              <RunGroup
                title="Failed"
                runs={groupedRuns.failed}
                currentRunId={currentRunId}
              />
            )}

            {filteredRuns.length === 0 && (
              <div className="text-center text-gray-500 py-8">
                {searchQuery ? 'No runs match your search' : 'No runs found'}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

interface RunGroupProps {
  title: string
  runs: Array<{ run_id: string; status: RunStatus; started_at: string; provider: string; model: string }>
  currentRunId?: string
}

function RunGroup({ title, runs, currentRunId }: RunGroupProps) {
  return (
    <div>
      <h3 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">
        {title} ({runs.length})
      </h3>
      <div className="space-y-1">
        {runs.map((run) => {
          const StatusIcon = STATUS_ICONS[run.status]
          return (
            <Link
              key={run.run_id}
              to={`/runs/${run.run_id}`}
              className={classNames(
                'block p-3 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 focus-ring transition-colors',
                currentRunId === run.run_id && 'bg-primary-100 dark:bg-primary-900 border-primary-200 dark:border-primary-800'
              )}
            >
              <div className="flex items-start gap-3">
                <StatusIcon className={classNames('h-5 w-5 mt-0.5', STATUS_COLORS[run.status])} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                    {run.run_id}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    {run.provider} • {run.model}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    {new Date(run.started_at).toLocaleString()}
                  </div>
                </div>
              </div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
