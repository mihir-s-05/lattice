import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ShareIcon,
  ArrowPathIcon,
  CheckCircleIcon,
  XCircleIcon,
  ClockIcon,
  ExclamationTriangleIcon,
} from '@heroicons/react/24/outline'
import { useAppStore } from '@/store'
import { apiClient } from '@/api/client'
import type { PlanGraph, PlanGraphSegment } from '@/types'
import classNames from 'classnames'

export function PlanGraphPanel() {
  const { currentRunId, runStates } = useAppStore()
  const [viewMode, setViewMode] = useState<'list' | 'graph'>('list')
  
  const { data: planGraph, isLoading } = useQuery({
    queryKey: ['plan-graph', currentRunId],
    queryFn: () => currentRunId ? apiClient.getPlanGraph(currentRunId) : Promise.resolve(null),
    enabled: !!currentRunId,
    refetchInterval: 5000, // Refresh every 5 seconds
  })

  if (!currentRunId) {
    return <div className="text-sm text-gray-500 dark:text-gray-400">No run selected</div>
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-6">
        <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600"></div>
      </div>
    )
  }

  if (!planGraph || planGraph.segments.length === 0) {
    return (
      <div className="text-center py-6">
        <ShareIcon className="h-8 w-8 text-gray-400 mx-auto mb-2" />
        <p className="text-sm text-gray-500 dark:text-gray-400">No plan graph available</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">
          Plan Segments ({planGraph.segments.length})
        </h4>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setViewMode('list')}
            className={classNames(
              'px-2 py-1 text-xs rounded focus-ring',
              viewMode === 'list' 
                ? 'bg-primary-100 text-primary-700 dark:bg-primary-900 dark:text-primary-300'
                : 'text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700'
            )}
          >
            List
          </button>
          <button
            onClick={() => setViewMode('graph')}
            className={classNames(
              'px-2 py-1 text-xs rounded focus-ring',
              viewMode === 'graph' 
                ? 'bg-primary-100 text-primary-700 dark:bg-primary-900 dark:text-primary-300'
                : 'text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700'
            )}
          >
            Graph
          </button>
        </div>
      </div>

      {/* Current Segment Info */}
      {planGraph.current_segment && (
        <div className="p-3 rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800">
          <div className="flex items-center gap-2 mb-1">
            <ArrowPathIcon className="h-4 w-4 text-blue-600 dark:text-blue-400" />
            <span className="text-sm font-medium text-blue-800 dark:text-blue-200">
              Current Segment: {planGraph.current_segment}
            </span>
          </div>
          {planGraph.last_switch_reason && (
            <p className="text-xs text-blue-700 dark:text-blue-300">
              Last switch: {planGraph.last_switch_reason}
            </p>
          )}
        </div>
      )}

      {/* Segments */}
      {viewMode === 'list' ? (
        <PlanSegmentsList segments={planGraph.segments} currentSegment={planGraph.current_segment} />
      ) : (
        <PlanGraphVisualization segments={planGraph.segments} currentSegment={planGraph.current_segment} />
      )}
    </div>
  )
}

interface PlanSegmentsListProps {
  segments: PlanGraphSegment[]
  currentSegment: string
}

function PlanSegmentsList({ segments, currentSegment }: PlanSegmentsListProps) {
  return (
    <div className="space-y-2">
      {segments.map((segment, index) => (
        <SegmentCard
          key={segment.id}
          segment={segment}
          isCurrent={segment.id === currentSegment}
          index={index}
        />
      ))}
    </div>
  )
}

interface SegmentCardProps {
  segment: PlanGraphSegment
  isCurrent: boolean
  index: number
}

function SegmentCard({ segment, isCurrent, index }: SegmentCardProps) {
  const getStatusIcon = () => {
    switch (segment.status) {
      case 'completed':
        return <CheckCircleIcon className="h-4 w-4 text-green-600 dark:text-green-400" />
      case 'failed':
        return <XCircleIcon className="h-4 w-4 text-red-600 dark:text-red-400" />
      case 'active':
      default:
        return <ClockIcon className="h-4 w-4 text-blue-600 dark:text-blue-400" />
    }
  }

  const getStatusColor = () => {
    switch (segment.status) {
      case 'completed':
        return 'border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/10'
      case 'failed':
        return 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/10'
      case 'active':
      default:
        return 'border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/10'
    }
  }

  return (
    <div className={classNames(
      'p-3 rounded-lg border transition-all',
      getStatusColor(),
      isCurrent && 'ring-2 ring-primary-500 ring-opacity-50'
    )}>
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-3 flex-1">
          <div className="flex flex-col items-center">
            <div className={classNames(
              'w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium',
              segment.status === 'completed' ? 'bg-green-600 text-white' :
              segment.status === 'failed' ? 'bg-red-600 text-white' :
              'bg-blue-600 text-white'
            )}>
              {index + 1}
            </div>
            {index < 3 && ( // Show connector for first few items
              <div className="w-px h-6 bg-gray-300 dark:bg-gray-600 mt-1" />
            )}
          </div>
          
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              {getStatusIcon()}
              <h5 className="font-medium text-sm text-gray-900 dark:text-gray-100">
                {segment.mode}
              </h5>
              {isCurrent && (
                <span className="px-2 py-1 rounded-full text-xs font-medium bg-primary-100 text-primary-800 dark:bg-primary-900 dark:text-primary-200">
                  Current
                </span>
              )}
              {segment.critical_path && (
                <ExclamationTriangleIcon className="h-4 w-4 text-amber-500" title="Critical Path" />
              )}
            </div>
            
            <div className="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400">
              <span className="capitalize">{segment.status}</span>
              {segment.critical_path && (
                <span className="text-amber-600 dark:text-amber-400">Critical Path</span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

interface PlanGraphVisualizationProps {
  segments: PlanGraphSegment[]
  currentSegment: string
}

function PlanGraphVisualization({ segments, currentSegment }: PlanGraphVisualizationProps) {
  return (
    <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800/50">
      {/* Simple node graph visualization */}
      <div className="space-y-4">
        <div className="text-center text-sm text-gray-600 dark:text-gray-400 mb-4">
          Execution Flow
        </div>
        
        <div className="flex flex-wrap justify-center gap-3">
          {segments.map((segment, index) => {
            const isCurrent = segment.id === currentSegment
            
            return (
              <div key={segment.id} className="flex items-center">
                <div
                  className={classNames(
                    'relative px-3 py-2 rounded-lg border-2 text-xs font-medium transition-all',
                    segment.status === 'completed' && 'border-green-500 bg-green-100 dark:bg-green-900/20 text-green-800 dark:text-green-200',
                    segment.status === 'failed' && 'border-red-500 bg-red-100 dark:bg-red-900/20 text-red-800 dark:text-red-200',
                    segment.status === 'active' && 'border-blue-500 bg-blue-100 dark:bg-blue-900/20 text-blue-800 dark:text-blue-200',
                    isCurrent && 'ring-2 ring-primary-500 ring-opacity-50',
                    segment.critical_path && 'shadow-md'
                  )}
                  title={`${segment.mode} - ${segment.status}${segment.critical_path ? ' (Critical)' : ''}`}
                >
                  <div className="flex items-center gap-1">
                    {segment.status === 'completed' && <CheckCircleIcon className="h-3 w-3" />}
                    {segment.status === 'failed' && <XCircleIcon className="h-3 w-3" />}
                    {segment.status === 'active' && <ClockIcon className="h-3 w-3" />}
                    <span className="truncate max-w-16" title={segment.mode}>
                      {segment.mode}
                    </span>
                  </div>
                  
                  {segment.critical_path && (
                    <div className="absolute -top-1 -right-1 w-2 h-2 bg-amber-500 rounded-full" />
                  )}
                  
                  {isCurrent && (
                    <div className="absolute -bottom-2 left-1/2 transform -translate-x-1/2">
                      <div className="w-1 h-1 bg-primary-500 rounded-full animate-pulse" />
                    </div>
                  )}
                </div>
                
                {index < segments.length - 1 && (
                  <div className="flex items-center mx-2">
                    <div className="w-4 h-px bg-gray-300 dark:bg-gray-600" />
                    <div className="w-0 h-0 border-l-4 border-l-gray-300 dark:border-l-gray-600 border-y-2 border-y-transparent ml-1" />
                  </div>
                )}
              </div>
            )
          })}
        </div>
        
        {/* Legend */}
        <div className="flex justify-center gap-4 text-xs text-gray-500 dark:text-gray-400 mt-6 pt-4 border-t border-gray-200 dark:border-gray-600">
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 bg-amber-500 rounded-full" />
            <span>Critical Path</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 bg-primary-500 rounded-full animate-pulse" />
            <span>Current</span>
          </div>
        </div>
      </div>
    </div>
  )
}
