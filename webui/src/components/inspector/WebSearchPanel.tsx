import React, { useState } from 'react'
import {
  MagnifyingGlassIcon,
  GlobeAltIcon,
  ClockIcon,
  DocumentTextIcon,
  ArrowTopRightOnSquareIcon,
} from '@heroicons/react/24/outline'
import { useAppStore } from '@/store'
import { MarkdownRenderer } from '../common/MarkdownRenderer'
import type { WebSearchQuery, WebSearchResult, WebSearchExtract } from '@/types'
import classNames from 'classnames'

export function WebSearchPanel() {
  const { currentRunId, runStates } = useAppStore()
  const [selectedQuery, setSelectedQuery] = useState<WebSearchQuery | null>(null)
  
  if (!currentRunId) {
    return <div className="text-sm text-gray-500 dark:text-gray-400">No run selected</div>
  }

  const runState = runStates[currentRunId]
  const webSearchQueries = runState?.webSearchQueries || []

  if (webSearchQueries.length === 0) {
    return (
      <div className="text-center py-6">
        <MagnifyingGlassIcon className="h-8 w-8 text-gray-400 mx-auto mb-2" />
        <p className="text-sm text-gray-500 dark:text-gray-400">No web searches yet</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Search Queries List */}
      <div className="space-y-2">
        {webSearchQueries.map((query) => (
          <WebSearchCard
            key={query.id}
            query={query}
            isSelected={selectedQuery?.id === query.id}
            onClick={() => setSelectedQuery(selectedQuery?.id === query.id ? null : query)}
          />
        ))}
      </div>
    </div>
  )
}

interface WebSearchCardProps {
  query: WebSearchQuery
  isSelected: boolean
  onClick: () => void
}

function WebSearchCard({ query, isSelected, onClick }: WebSearchCardProps) {
  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
      {/* Header */}
      <button
        onClick={onClick}
        className="w-full p-3 text-left hover:bg-gray-50 dark:hover:bg-gray-800/50 focus-ring transition-colors"
      >
        <div className="flex items-start justify-between">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <MagnifyingGlassIcon className="h-4 w-4 text-gray-400" />
              <span className="font-medium text-sm text-gray-900 dark:text-gray-100 truncate">
                {query.query}
              </span>
            </div>
            <div className="flex items-center gap-4 text-xs text-gray-500 dark:text-gray-400">
              <span>{query.source}</span>
              <span>•</span>
              <span>{query.results_count} results</span>
              <span>•</span>
              <span>{query.latency_ms}ms</span>
              <span>•</span>
              <span>{new Date(query.timestamp).toLocaleTimeString()}</span>
            </div>
          </div>
          <div className="flex items-center gap-2 ml-3">
            <span className="badge badge-websearch">🔎</span>
            <div className={classNames(
              'transform transition-transform',
              isSelected ? 'rotate-180' : ''
            )}>
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </div>
          </div>
        </div>
      </button>

      {/* Expanded Content */}
      {isSelected && (
        <div className="border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
          <div className="p-4 space-y-4">
            {/* Query Details */}
            <div className="grid grid-cols-2 gap-4 text-xs">
              <div>
                <span className="text-gray-500 dark:text-gray-400">Source:</span>
                <span className="ml-2 font-mono">{query.source}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-gray-400">Engines:</span>
                <span className="ml-2">{query.engines.join(', ')}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-gray-400">Results:</span>
                <span className="ml-2">{query.results_count}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-gray-400">URLs Fetched:</span>
                <span className="ml-2">{query.urls_fetched}</span>
              </div>
              {query.time_range && (
                <div className="col-span-2">
                  <span className="text-gray-500 dark:text-gray-400">Time Range:</span>
                  <span className="ml-2">{query.time_range}</span>
                </div>
              )}
            </div>

            {/* Search Results */}
            {query.results.length > 0 && (
              <div>
                <h5 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
                  Results ({query.results.length})
                </h5>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {query.results.map((result, index) => (
                    <SearchResultCard key={index} result={result} />
                  ))}
                </div>
              </div>
            )}

            {/* Extracts */}
            {query.extracts.length > 0 && (
              <div>
                <h5 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
                  Extracts ({query.extracts.length})
                </h5>
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {query.extracts.map((extract, index) => (
                    <ExtractCard key={index} extract={extract} />
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

interface SearchResultCardProps {
  result: WebSearchResult
}

function SearchResultCard({ result }: SearchResultCardProps) {
  const handleClick = () => {
    window.open(result.url, '_blank', 'noopener,noreferrer')
  }

  return (
    <button
      onClick={handleClick}
      className="w-full text-left p-2 rounded border border-gray-200 dark:border-gray-600 hover:bg-white dark:hover:bg-gray-700 focus-ring transition-colors"
    >
      <div className="flex items-start gap-2">
        <GlobeAltIcon className="h-4 w-4 text-gray-400 mt-0.5 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm text-gray-900 dark:text-gray-100 truncate mb-1">
            {result.title}
          </div>
          <div className="text-xs text-gray-600 dark:text-gray-400 line-clamp-2 mb-1">
            {result.snippet}
          </div>
          <div className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
            <ArrowTopRightOnSquareIcon className="h-3 w-3" />
            <span className="truncate">{result.url}</span>
          </div>
        </div>
      </div>
    </button>
  )
}

interface ExtractCardProps {
  extract: WebSearchExtract
}

function ExtractCard({ extract }: ExtractCardProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  const handleUrlClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    window.open(extract.url, '_blank', 'noopener,noreferrer')
  }

  return (
    <div className="border border-gray-200 dark:border-gray-600 rounded-lg overflow-hidden">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full text-left p-3 hover:bg-white dark:hover:bg-gray-700 focus-ring transition-colors"
      >
        <div className="flex items-start justify-between">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <DocumentTextIcon className="h-4 w-4 text-gray-400" />
              <button
                onClick={handleUrlClick}
                className="text-sm text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 truncate focus-ring rounded"
              >
                {extract.url}
              </button>
            </div>
            <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
              <ClockIcon className="h-3 w-3" />
              <span>Extracted {new Date(extract.extracted_at).toLocaleString()}</span>
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

      {isExpanded && (
        <div className="border-t border-gray-200 dark:border-gray-600 p-3 bg-gray-50 dark:bg-gray-800">
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <MarkdownRenderer content={extract.content} />
          </div>
        </div>
      )}
    </div>
  )
}
