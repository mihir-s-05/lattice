import React, { useState } from 'react'
import { Dialog, Transition } from '@headlessui/react'
import { Fragment } from 'react'
import {
  UserGroupIcon,
  XMarkIcon,
  DocumentTextIcon,
  CalendarIcon,
  ClockIcon,
} from '@heroicons/react/24/outline'
import { useAppStore } from '@/store'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import { MarkdownRenderer } from '../common/MarkdownRenderer'
import type { Huddle } from '@/types'
import classNames from 'classnames'

export function HuddlesPanel() {
  const { currentRunId, runStates } = useAppStore()
  const [selectedHuddle, setSelectedHuddle] = useState<Huddle | null>(null)
  
  if (!currentRunId) {
    return <div className="text-sm text-gray-500 dark:text-gray-400">No run selected</div>
  }

  const runState = runStates[currentRunId]
  const huddles = runState ? Object.values(runState.huddles) : []

  const activeHuddles = huddles.filter(h => h.status === 'active')
  const completedHuddles = huddles.filter(h => h.status === 'completed')

  if (huddles.length === 0) {
    return (
      <div className="text-center py-6">
        <UserGroupIcon className="h-8 w-8 text-gray-400 mx-auto mb-2" />
        <p className="text-sm text-gray-500 dark:text-gray-400">No huddles yet</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Active Huddles */}
      {activeHuddles.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
            Active ({activeHuddles.length})
          </h4>
          <div className="space-y-2">
            {activeHuddles.map((huddle) => (
              <HuddleCard
                key={huddle.id}
                huddle={huddle}
                onClick={() => setSelectedHuddle(huddle)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Completed Huddles */}
      {completedHuddles.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-2">
            Completed ({completedHuddles.length})
          </h4>
          <div className="space-y-2">
            {completedHuddles.map((huddle) => (
              <HuddleCard
                key={huddle.id}
                huddle={huddle}
                onClick={() => setSelectedHuddle(huddle)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Huddle Modal */}
      <HuddleModal
        huddle={selectedHuddle}
        isOpen={selectedHuddle !== null}
        onClose={() => setSelectedHuddle(null)}
      />
    </div>
  )
}

interface HuddleCardProps {
  huddle: Huddle
  onClick: () => void
}

function HuddleCard({ huddle, onClick }: HuddleCardProps) {
  return (
    <button
      onClick={onClick}
      className="w-full text-left p-3 rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800/50 focus-ring transition-colors"
    >
      <div className="flex items-start justify-between mb-2">
        <h5 className="font-medium text-sm text-gray-900 dark:text-gray-100 truncate flex-1">
          {huddle.topic}
        </h5>
        <div className={classNames(
          'px-2 py-1 rounded-full text-xs font-medium ml-2',
          huddle.status === 'active' 
            ? 'bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-400'
            : 'bg-gray-100 text-gray-800 dark:bg-gray-900/20 dark:text-gray-400'
        )}>
          {huddle.status === 'active' ? (
            <>● Active</>
          ) : (
            <>✓ Complete</>
          )}
        </div>
      </div>
      
      <div className="flex items-center gap-4 text-xs text-gray-500 dark:text-gray-400">
        <div className="flex items-center gap-1">
          <UserGroupIcon className="h-3 w-3" />
          <span>{huddle.attendees.length}</span>
        </div>
        <div className="flex items-center gap-1">
          <ClockIcon className="h-3 w-3" />
          <span>{new Date(huddle.started_at).toLocaleTimeString()}</span>
        </div>
      </div>
      
      <div className="flex flex-wrap gap-1 mt-2">
        {huddle.attendees.slice(0, 3).map((attendee) => (
          <span
            key={attendee}
            className="inline-flex items-center px-2 py-1 rounded-md text-xs bg-blue-100 text-blue-800 dark:bg-blue-900/20 dark:text-blue-400"
          >
            {attendee}
          </span>
        ))}
        {huddle.attendees.length > 3 && (
          <span className="inline-flex items-center px-2 py-1 rounded-md text-xs bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">
            +{huddle.attendees.length - 3}
          </span>
        )}
      </div>
    </button>
  )
}

interface HuddleModalProps {
  huddle: Huddle | null
  isOpen: boolean
  onClose: () => void
}

function HuddleModal({ huddle, isOpen, onClose }: HuddleModalProps) {
  const { data: transcript, isLoading } = useQuery({
    queryKey: ['huddle-transcript', huddle?.id],
    queryFn: async () => {
      if (!huddle?.transcript_path) return null
      // This would fetch the transcript from the artifacts API
      // For now, return mock data
      return `# Huddle: ${huddle.topic}

## Participants
${huddle.attendees.map(a => `- ${a}`).join('\n')}

## Discussion
This is a mock transcript. In a real implementation, this would fetch the actual huddle transcript from the artifacts API.

**Agent A**: I think we need to discuss the database schema approach.

**Agent B**: Agreed. Should we use a relational or document-based approach?

**Agent A**: For this use case, I'd recommend PostgreSQL with JSONB columns for flexibility.

## Decisions Made
- Use PostgreSQL as the primary database
- Implement JSONB columns for flexible data storage
- Set up proper indexing for query performance

## Next Steps
- Agent A will create the database schema
- Agent B will set up the connection pooling
- Review and test the implementation together`
    },
    enabled: isOpen && !!huddle?.transcript_path,
  })

  return (
    <Transition appear show={isOpen} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={onClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-300"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-200"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black bg-opacity-25" />
        </Transition.Child>

        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4 text-center">
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-300"
              enterFrom="opacity-0 scale-95"
              enterTo="opacity-100 scale-100"
              leave="ease-in duration-200"
              leaveFrom="opacity-100 scale-100"
              leaveTo="opacity-0 scale-95"
            >
              <Dialog.Panel className="w-full max-w-4xl transform overflow-hidden rounded-2xl bg-white dark:bg-gray-800 text-left align-middle shadow-xl transition-all">
                {huddle && (
                  <>
                    {/* Header */}
                    <div className="flex items-center justify-between p-6 border-b border-gray-200 dark:border-gray-700">
                      <div>
                        <Dialog.Title as="h3" className="text-lg font-medium leading-6 text-gray-900 dark:text-gray-100">
                          {huddle.topic}
                        </Dialog.Title>
                        <div className="flex items-center gap-4 mt-2 text-sm text-gray-500 dark:text-gray-400">
                          <div className="flex items-center gap-1">
                            <UserGroupIcon className="h-4 w-4" />
                            <span>{huddle.attendees.length} participants</span>
                          </div>
                          <div className="flex items-center gap-1">
                            <CalendarIcon className="h-4 w-4" />
                            <span>Started {new Date(huddle.started_at).toLocaleString()}</span>
                          </div>
                          {huddle.completed_at && (
                            <div className="flex items-center gap-1">
                              <ClockIcon className="h-4 w-4" />
                              <span>Completed {new Date(huddle.completed_at).toLocaleString()}</span>
                            </div>
                          )}
                        </div>
                      </div>
                      <button
                        onClick={onClose}
                        className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus-ring rounded"
                      >
                        <XMarkIcon className="h-6 w-6" />
                      </button>
                    </div>

                    {/* Content */}
                    <div className="p-6">
                      {/* Participants */}
                      <div className="mb-6">
                        <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-3">
                          Participants
                        </h4>
                        <div className="flex flex-wrap gap-2">
                          {huddle.attendees.map((attendee) => (
                            <span
                              key={attendee}
                              className="inline-flex items-center px-3 py-1 rounded-full text-sm bg-blue-100 text-blue-800 dark:bg-blue-900/20 dark:text-blue-400"
                            >
                              {attendee}
                            </span>
                          ))}
                        </div>
                      </div>

                      {/* Transcript */}
                      <div>
                        <div className="flex items-center justify-between mb-3">
                          <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">
                            Transcript
                          </h4>
                          {huddle.transcript_path && (
                            <button className="text-sm text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 focus-ring rounded px-2 py-1">
                              <DocumentTextIcon className="h-4 w-4 inline mr-1" />
                              Open in artifacts
                            </button>
                          )}
                        </div>
                        
                        {isLoading ? (
                          <div className="flex items-center justify-center py-8">
                            <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600"></div>
                          </div>
                        ) : transcript ? (
                          <div className="prose prose-sm dark:prose-invert max-w-none bg-gray-50 dark:bg-gray-900 rounded-lg p-4 max-h-96 overflow-y-auto">
                            <MarkdownRenderer content={transcript} />
                          </div>
                        ) : (
                          <div className="text-center py-8 text-gray-500 dark:text-gray-400">
                            <DocumentTextIcon className="h-8 w-8 mx-auto mb-2" />
                            <p>No transcript available</p>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Footer */}
                    <div className="flex justify-end gap-3 px-6 py-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50">
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={onClose}
                      >
                        Close
                      </button>
                    </div>
                  </>
                )}
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition>
  )
}
