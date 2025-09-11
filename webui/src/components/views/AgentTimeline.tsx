import React from 'react'
import { FixedSizeList as List } from 'react-window'
import AutoSizer from 'react-virtualized-auto-sizer'
import {
  LightBulbIcon,
  PlayIcon,
  DocumentTextIcon,
  WrenchScrewdriverIcon,
  MagnifyingGlassIcon,
} from '@heroicons/react/24/outline'
import { MarkdownRenderer } from '../common/MarkdownRenderer'
import type { AgentTurn } from '@/types'
import classNames from 'classnames'

interface AgentTimelineProps {
  agentId: string
  runId: string
  turns: AgentTurn[]
}

const PHASE_CONFIGS = {
  plan: {
    icon: LightBulbIcon,
    color: 'text-blue-600 dark:text-blue-400',
    bgColor: 'bg-blue-50 dark:bg-blue-900/10',
    borderColor: 'border-blue-200 dark:border-blue-800',
    title: 'Planning',
  },
  act: {
    icon: PlayIcon,
    color: 'text-green-600 dark:text-green-400',
    bgColor: 'bg-green-50 dark:bg-green-900/10',
    borderColor: 'border-green-200 dark:border-green-800',
    title: 'Acting',
  },
  report: {
    icon: DocumentTextIcon,
    color: 'text-purple-600 dark:text-purple-400',
    bgColor: 'bg-purple-50 dark:bg-purple-900/10',
    borderColor: 'border-purple-200 dark:border-purple-800',
    title: 'Reporting',
  },
}

export function AgentTimeline({ agentId, runId, turns }: AgentTimelineProps) {
  if (turns.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center p-4">
        <div className="text-center text-gray-500 dark:text-gray-400">
          <div className="text-3xl mb-2">⏳</div>
          <p className="text-sm">No activity yet</p>
        </div>
      </div>
    )
  }

  const renderTurn = ({ index, style }: { index: number; style: React.CSSProperties }) => {
    const turn = turns[index]
    const config = PHASE_CONFIGS[turn.phase]
    const Icon = config.icon

    return (
      <div style={style} className="px-3 py-2">
        <div className={classNames(
          'rounded-lg border p-3 hover:shadow-sm transition-shadow',
          config.bgColor,
          config.borderColor
        )}>
          {/* Turn Header */}
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <Icon className={classNames('h-4 w-4', config.color)} />
              <span className={classNames('text-sm font-medium', config.color)}>
                {config.title}
              </span>
            </div>
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {new Date(turn.timestamp).toLocaleTimeString()}
            </span>
          </div>

          {/* Turn Content */}
          <div className="prose prose-sm dark:prose-invert max-w-none mb-3">
            <MarkdownRenderer content={turn.content} />
          </div>

          {/* Turn Metadata */}
          <div className="space-y-2">
            {/* Artifacts */}
            {turn.artifacts_written.length > 0 && (
              <div className="flex items-start gap-2">
                <DocumentTextIcon className="h-4 w-4 text-gray-400 mt-0.5" />
                <div className="flex-1">
                  <div className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Artifacts ({turn.artifacts_written.length})
                  </div>
                  <div className="space-y-1">
                    {turn.artifacts_written.map((artifact, idx) => (
                      <div key={idx} className="text-xs font-mono text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded">
                        {artifact}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* RAG Queries */}
            {turn.rag_queries.length > 0 && (
              <div className="flex items-start gap-2">
                <MagnifyingGlassIcon className="h-4 w-4 text-gray-400 mt-0.5" />
                <div className="flex-1">
                  <div className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                    RAG Queries ({turn.rag_queries.length})
                  </div>
                  <div className="space-y-1">
                    {turn.rag_queries.map((query, idx) => (
                      <div key={idx} className="text-xs text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded">
                        {query}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* Tool Calls */}
            {turn.tool_calls.length > 0 && (
              <div className="flex items-start gap-2">
                <WrenchScrewdriverIcon className="h-4 w-4 text-gray-400 mt-0.5" />
                <div className="flex-1">
                  <div className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Tool Calls ({turn.tool_calls.length})
                  </div>
                  <div className="space-y-1">
                    {turn.tool_calls.map((call, idx) => (
                      <div key={idx} className="text-xs bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded">
                        <div className="font-mono text-gray-800 dark:text-gray-200">{call.name}</div>
                        {Object.keys(call.arguments).length > 0 && (
                          <div className="text-gray-600 dark:text-gray-400 mt-1">
                            {Object.entries(call.arguments).map(([key, value]) => (
                              <div key={key} className="truncate">
                                <span className="font-medium">{key}:</span> {JSON.stringify(value)}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full">
      <AutoSizer>
        {({ height, width }) => (
          <List
            height={height}
            width={width}
            itemCount={turns.length}
            itemSize={200} // Approximate height per turn
            overscanCount={3}
          >
            {renderTurn}
          </List>
        )}
      </AutoSizer>
    </div>
  )
}
