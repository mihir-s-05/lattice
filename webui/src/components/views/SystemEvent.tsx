import React from 'react'
import {
  ArrowPathIcon,
  ServerIcon,
  CheckCircleIcon,
  XCircleIcon,
  DocumentIcon,
} from '@heroicons/react/24/outline'
import type { SystemEvent as SystemEventType } from '@/types'
import classNames from 'classnames'

interface SystemEventProps {
  event: SystemEventType
}

const EVENT_CONFIGS = {
  plan_switch: {
    icon: ArrowPathIcon,
    color: 'text-purple-600 dark:text-purple-400',
    bgColor: 'bg-purple-100 dark:bg-purple-900/20',
    title: 'Plan Switch',
  },
  provider_switch: {
    icon: ServerIcon,
    color: 'text-orange-600 dark:text-orange-400',
    bgColor: 'bg-orange-100 dark:bg-orange-900/20',
    title: 'Provider Switch',
  },
  gate_eval: {
    icon: CheckCircleIcon,
    color: 'text-green-600 dark:text-green-400',
    bgColor: 'bg-green-100 dark:bg-green-900/20',
    title: 'Gate Evaluation',
  },
  contract_test_result: {
    icon: XCircleIcon,
    color: 'text-red-600 dark:text-red-400',
    bgColor: 'bg-red-100 dark:bg-red-900/20',
    title: 'Contract Test',
  },
  finalization: {
    icon: DocumentIcon,
    color: 'text-blue-600 dark:text-blue-400',
    bgColor: 'bg-blue-100 dark:bg-blue-900/20',
    title: 'Finalization',
  },
}

export function SystemEvent({ event }: SystemEventProps) {
  const config = EVENT_CONFIGS[event.type as keyof typeof EVENT_CONFIGS] || {
    icon: DocumentIcon,
    color: 'text-gray-600 dark:text-gray-400',
    bgColor: 'bg-gray-100 dark:bg-gray-900/20',
    title: event.type,
  }

  const Icon = config.icon

  const renderEventDetails = () => {
    switch (event.type) {
      case 'plan_switch':
        return (
          <div className="text-sm text-gray-700 dark:text-gray-300">
            Switched from <span className="font-mono">{event.data.from_mode}</span> to{' '}
            <span className="font-mono">{event.data.to_mode}</span>
            <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Reason: {event.data.reason_type} - {event.data.details}
            </div>
          </div>
        )

      case 'provider_switch':
        return (
          <div className="text-sm text-gray-700 dark:text-gray-300">
            Switched from <span className="font-mono">{event.data.from}</span> to{' '}
            <span className="font-mono">{event.data.to}</span>
            {event.data.reason && (
              <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Reason: {event.data.reason}
              </div>
            )}
          </div>
        )

      case 'finalization':
        return (
          <div className="text-sm text-gray-700 dark:text-gray-300">
            Run finalized
            <div className="mt-2 space-y-1">
              {event.data.report_path && (
                <div className="text-xs">
                  <span className="text-gray-500 dark:text-gray-400">Report:</span>{' '}
                  <span className="font-mono">{event.data.report_path}</span>
                </div>
              )}
              {event.data.deliverables_path && (
                <div className="text-xs">
                  <span className="text-gray-500 dark:text-gray-400">Deliverables:</span>{' '}
                  <span className="font-mono">{event.data.deliverables_path}</span>
                </div>
              )}
            </div>
          </div>
        )

      default:
        return (
          <div className="text-sm text-gray-700 dark:text-gray-300">
            <pre className="text-xs font-mono whitespace-pre-wrap overflow-x-auto">
              {JSON.stringify(event.data, null, 2)}
            </pre>
          </div>
        )
    }
  }

  return (
    <div className={classNames('flex gap-4 p-4 rounded-lg', config.bgColor)}>
      {/* Icon */}
      <div className="flex-shrink-0">
        <Icon className={classNames('h-5 w-5', config.color)} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <span className={classNames('font-medium text-sm', config.color)}>
            {config.title}
          </span>
          <span className="text-xs text-gray-500 dark:text-gray-400">
            {new Date(event.timestamp).toLocaleTimeString()}
          </span>
        </div>

        {/* Event Details */}
        {renderEventDetails()}
      </div>
    </div>
  )
}
