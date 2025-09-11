import React from 'react'
import {
  DocumentTextIcon,
  ExclamationTriangleIcon,
  CheckCircleIcon,
  LinkIcon,
  FolderIcon,
  GlobeAltIcon,
} from '@heroicons/react/24/outline'
import { useAppStore } from '@/store'
import { MarkdownRenderer } from '../common/MarkdownRenderer'
import type { DecisionSummary } from '@/types'
import classNames from 'classnames'

export function DecisionsPanel() {
  const { currentRunId, runStates } = useAppStore()
  
  if (!currentRunId) {
    return <div className="text-sm text-gray-500 dark:text-gray-400">No run selected</div>
  }

  const runState = runStates[currentRunId]
  const decisions = runState ? Object.values(runState.decisions) : []

  if (decisions.length === 0) {
    return (
      <div className="text-center py-6">
        <DocumentTextIcon className="h-8 w-8 text-gray-400 mx-auto mb-2" />
        <p className="text-sm text-gray-500 dark:text-gray-400">No decisions yet</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {decisions.map((decision) => (
        <DecisionCard key={decision.id} decision={decision} />
      ))}
    </div>
  )
}

interface DecisionCardProps {
  decision: DecisionSummary
}

function DecisionCard({ decision }: DecisionCardProps) {
  const [isExpanded, setIsExpanded] = React.useState(false)

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full p-4 text-left hover:bg-gray-50 dark:hover:bg-gray-800/50 focus-ring transition-colors"
      >
        <div className="flex items-start justify-between">
          <div className="flex-1 min-w-0">
            <h4 className="font-medium text-sm text-gray-900 dark:text-gray-100 mb-1">
              {decision.topic}
            </h4>
            <p className="text-xs text-gray-600 dark:text-gray-400 line-clamp-2">
              {decision.decision}
            </p>
          </div>
          <div className="flex items-center gap-2 ml-3">
            <span className="badge badge-decision">◆</span>
            <div className={classNames(
              'transform transition-transform',
              isExpanded ? 'rotate-180' : ''
            )}>
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </div>
          </div>
        </div>
        
        <div className="flex items-center gap-4 mt-2 text-xs text-gray-500 dark:text-gray-400">
          <span>{new Date(decision.created_at).toLocaleString()}</span>
          {decision.sources.length > 0 && (
            <span>{decision.sources.length} source{decision.sources.length !== 1 ? 's' : ''}</span>
          )}
          {decision.actions.length > 0 && (
            <span>{decision.actions.length} action{decision.actions.length !== 1 ? 's' : ''}</span>
          )}
        </div>
      </button>

      {/* Expanded Content */}
      {isExpanded && (
        <div className="border-t border-gray-200 dark:border-gray-700 p-4 bg-gray-50 dark:bg-gray-800/50">
          <div className="space-y-4">
            {/* Decision Details */}
            <div>
              <h5 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
                Decision
              </h5>
              <div className="prose prose-sm dark:prose-invert max-w-none">
                <MarkdownRenderer content={decision.decision} />
              </div>
            </div>

            {/* Rationale */}
            <div>
              <h5 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
                Rationale
              </h5>
              <div className="prose prose-sm dark:prose-invert max-w-none">
                <MarkdownRenderer content={decision.rationale} />
              </div>
            </div>

            {/* Risks */}
            {decision.risks.length > 0 && (
              <div>
                <h5 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
                  Risks
                </h5>
                <div className="space-y-2">
                  {decision.risks.map((risk, index) => (
                    <div key={index} className="flex items-start gap-2 p-2 rounded bg-yellow-50 dark:bg-yellow-900/20">
                      <ExclamationTriangleIcon className="h-4 w-4 text-yellow-600 dark:text-yellow-400 mt-0.5" />
                      <span className="text-sm text-gray-700 dark:text-gray-300">{risk}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Actions */}
            {decision.actions.length > 0 && (
              <div>
                <h5 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
                  Actions
                </h5>
                <div className="space-y-2">
                  {decision.actions.map((action, index) => (
                    <div key={index} className="flex items-start gap-2 p-2 rounded bg-blue-50 dark:bg-blue-900/20">
                      <CheckCircleIcon className="h-4 w-4 text-blue-600 dark:text-blue-400 mt-0.5" />
                      <div className="flex-1">
                        <div className="text-sm text-gray-700 dark:text-gray-300">{action.action}</div>
                        <div className="flex items-center gap-3 mt-1 text-xs text-gray-500 dark:text-gray-400">
                          <span>Owner: {action.owner}</span>
                          {action.deadline && (
                            <span>Due: {new Date(action.deadline).toLocaleDateString()}</span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Contracts */}
            {decision.contracts && (
              <div>
                <h5 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
                  Contracts
                </h5>
                <div className="p-2 rounded bg-gray-100 dark:bg-gray-800 font-mono text-xs text-gray-600 dark:text-gray-400">
                  {decision.contracts}
                </div>
              </div>
            )}

            {/* Sources */}
            {decision.sources.length > 0 && (
              <div>
                <h5 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
                  Sources
                </h5>
                <div className="space-y-2">
                  {decision.sources.map((source, index) => (
                    <SourceLink key={index} source={source} />
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

interface SourceLinkProps {
  source: DecisionSummary['sources'][0]
}

function SourceLink({ source }: SourceLinkProps) {
  const getSourceIcon = () => {
    switch (source.type) {
      case 'external':
        return <GlobeAltIcon className="h-4 w-4" />
      case 'artifact':
        return <FolderIcon className="h-4 w-4" />
      case 'rag':
        return <DocumentTextIcon className="h-4 w-4" />
      default:
        return <LinkIcon className="h-4 w-4" />
    }
  }

  const getSourceColor = () => {
    switch (source.type) {
      case 'external':
        return 'text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-900/20'
      case 'artifact':
        return 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20'
      case 'rag':
        return 'text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-900/20'
      default:
        return 'text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/20'
    }
  }

  const handleClick = () => {
    if (source.url) {
      window.open(source.url, '_blank', 'noopener,noreferrer')
    } else if (source.path) {
      // Navigate to artifact view
      console.log('Navigate to artifact:', source.path)
    }
  }

  return (
    <button
      onClick={handleClick}
      className={classNames(
        'flex items-center gap-2 p-2 rounded text-left w-full hover:opacity-80 focus-ring transition-opacity',
        getSourceColor()
      )}
    >
      {getSourceIcon()}
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">{source.title}</div>
        <div className="text-xs opacity-75 truncate">
          {source.type === 'external' ? source.url : source.path || source.type}
        </div>
      </div>
    </button>
  )
}
