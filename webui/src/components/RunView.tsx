import React, { useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { Tab } from '@headlessui/react'
import {
  ChatBubbleLeftRightIcon,
  RectangleGroupIcon,
  ExclamationTriangleIcon,
} from '@heroicons/react/24/outline'
import { ChatView } from './views/ChatView'
import { SwimlanesView } from './views/SwimlanesView'
import { useAppStore } from '@/store'
import { useWebSocket } from '@/hooks/useWebSocket'
import classNames from 'classnames'

export function RunView() {
  const { runId } = useParams<{ runId: string }>()
  const { preferences, updatePreferences, setCurrentRun, runs, runStates } = useAppStore()

  // Set current run and initialize WebSocket connection
  useEffect(() => {
    if (runId) {
      setCurrentRun(runId)
    }
    return () => setCurrentRun(null)
  }, [runId, setCurrentRun])

  // Setup WebSocket connection
  const { reconnect } = useWebSocket({
    runId: runId!,
  })

  if (!runId) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center text-gray-500 dark:text-gray-400">
          <ExclamationTriangleIcon className="h-12 w-12 mx-auto mb-4" />
          <p>No run selected</p>
        </div>
      </div>
    )
  }

  const run = runs[runId]
  const runState = runStates[runId]

  if (!run) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center text-gray-500 dark:text-gray-400">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600 mx-auto mb-4"></div>
          <p>Loading run...</p>
        </div>
      </div>
    )
  }

  const tabs = [
    {
      key: 'chat',
      name: 'Chat',
      icon: ChatBubbleLeftRightIcon,
      component: ChatView,
    },
    {
      key: 'swimlanes',
      name: 'Swimlanes',
      icon: RectangleGroupIcon,
      component: SwimlanesView,
    },
  ]

  const activeTabIndex = tabs.findIndex(tab => tab.key === preferences.activeView)

  return (
    <div className="flex-1 flex flex-col h-full">
      {/* Run Header */}
      <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
              {run.run_id}
            </h1>
            <div className="flex items-center gap-4 mt-1 text-sm text-gray-500 dark:text-gray-400">
              <span>{run.provider} • {run.model}</span>
              <span>•</span>
              <span>Started {new Date(run.started_at).toLocaleString()}</span>
              <span className={classNames('status-indicator', `status-${run.status}`)}>
                {run.status}
              </span>
            </div>
          </div>

          {/* Connection Status */}
          {runState && (
            <div className="flex items-center gap-2">
              {runState.isReconnecting ? (
                <div className="flex items-center gap-2 text-yellow-600 dark:text-yellow-400">
                  <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-current"></div>
                  <span className="text-sm">Reconnecting...</span>
                </div>
              ) : runState.isConnected ? (
                <div className="flex items-center gap-2 text-green-600 dark:text-green-400">
                  <div className="h-2 w-2 rounded-full bg-current animate-pulse"></div>
                  <span className="text-sm">Live</span>
                </div>
              ) : (
                <button
                  onClick={reconnect}
                  className="flex items-center gap-2 text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 text-sm focus-ring rounded px-2 py-1"
                >
                  <div className="h-2 w-2 rounded-full bg-current"></div>
                  <span>Disconnected - Click to reconnect</span>
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* View Tabs */}
      <Tab.Group
        selectedIndex={activeTabIndex >= 0 ? activeTabIndex : 0}
        onChange={(index) => updatePreferences({ activeView: tabs[index].key as 'chat' | 'swimlanes' })}
      >
        <Tab.List className="flex border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-6">
          {tabs.map((tab) => (
            <Tab
              key={tab.key}
              className={({ selected }) =>
                classNames(
                  'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 focus-ring',
                  selected
                    ? 'border-primary-500 text-primary-600 dark:text-primary-400'
                    : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300'
                )
              }
            >
              <tab.icon className="h-4 w-4" />
              {tab.name}
            </Tab>
          ))}
        </Tab.List>

        <Tab.Panels className="flex-1 min-h-0">
          {tabs.map((tab) => (
            <Tab.Panel key={tab.key} className="h-full focus-ring">
              <tab.component runId={runId} />
            </Tab.Panel>
          ))}
        </Tab.Panels>
      </Tab.Group>
    </div>
  )
}
