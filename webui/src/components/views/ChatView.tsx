import React, { useRef, useEffect, useState } from 'react'
import { FixedSizeList as List } from 'react-window'
import AutoSizer from 'react-virtualized-auto-sizer'
import { PaperAirplaneIcon } from '@heroicons/react/24/outline'
import { ChatMessage } from './ChatMessage'
import { SystemEvent } from './SystemEvent'
import { useAppStore } from '@/store'
import { apiClient } from '@/api/client'
import classNames from 'classnames'

interface ChatViewProps {
  runId: string
}

interface TimelineItem {
  id: string
  type: 'message' | 'event'
  timestamp: string
  data: any
}

export function ChatView({ runId }: ChatViewProps) {
  const listRef = useRef<List>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const [newMessage, setNewMessage] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  
  const { runStates, runs } = useAppStore()
  const runState = runStates[runId]
  const run = runs[runId]

  // Combine messages and events into timeline
  const timelineItems: TimelineItem[] = React.useMemo(() => {
    if (!runState) return []

    const items: TimelineItem[] = [
      ...runState.messages.map(msg => ({
        id: msg.id,
        type: 'message' as const,
        timestamp: msg.timestamp,
        data: msg,
      })),
      ...runState.events.map(event => ({
        id: event.id,
        type: 'event' as const,
        timestamp: event.timestamp,
        data: event,
      })),
    ]

    return items.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())
  }, [runState])

  // Auto-scroll to bottom when new items arrive
  useEffect(() => {
    if (listRef.current && timelineItems.length > 0) {
      listRef.current.scrollToItem(timelineItems.length - 1, 'end')
    }
  }, [timelineItems.length])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newMessage.trim() || isSubmitting) return

    setIsSubmitting(true)
    try {
      if (run?.status === 'running') {
        // For running runs, send message to router
        // This would need a WebSocket send message API
        console.log('Sending message to router:', newMessage)
      } else {
        // For new runs, start a new run
        await apiClient.startRun(newMessage.trim())
      }
      setNewMessage('')
    } catch (error) {
      console.error('Failed to send message:', error)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      handleSubmit(e as any)
    }
  }

  const renderItem = ({ index, style }: { index: number; style: React.CSSProperties }) => {
    const item = timelineItems[index]
    
    return (
      <div style={style} className="px-6 py-2">
        {item.type === 'message' ? (
          <ChatMessage message={item.data} />
        ) : (
          <SystemEvent event={item.data} />
        )}
      </div>
    )
  }

  if (!runState) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center text-gray-500 dark:text-gray-400">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600 mx-auto mb-4"></div>
          <p>Loading chat...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-white dark:bg-gray-900">
      {/* Chat Timeline */}
      <div className="flex-1 min-h-0">
        {timelineItems.length === 0 ? (
          <div className="flex-1 flex items-center justify-center h-full">
            <div className="text-center text-gray-500 dark:text-gray-400">
              <div className="text-6xl mb-4">💬</div>
              <h3 className="text-lg font-medium mb-2">No messages yet</h3>
              <p className="text-sm">
                {run?.status === 'running' 
                  ? 'Start chatting with the Router LLM below'
                  : 'Send a message to start a new run'
                }
              </p>
            </div>
          </div>
        ) : (
          <AutoSizer>
            {({ height, width }) => (
              <List
                ref={listRef}
                height={height}
                width={width}
                itemCount={timelineItems.length}
                itemSize={120} // Approximate height per item
                overscanCount={5}
              >
                {renderItem}
              </List>
            )}
          </AutoSizer>
        )}
      </div>

      {/* Chat Input */}
      <div className="border-t border-gray-200 dark:border-gray-700 p-4">
        <form onSubmit={handleSubmit} className="flex gap-3">
          <div className="flex-1">
            <textarea
              ref={inputRef}
              value={newMessage}
              onChange={(e) => setNewMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                run?.status === 'running'
                  ? 'Send message to Router LLM... (Cmd+Enter or Ctrl+Enter to send)'
                  : 'Enter prompt to start new run... (Cmd+Enter or Ctrl+Enter to send)'
              }
              className="input-field resize-none"
              rows={3}
              disabled={isSubmitting}
            />
            <div className="flex items-center justify-between mt-2">
              <div className="text-xs text-gray-500 dark:text-gray-400">
                {run?.status === 'running' ? (
                  <>Send to Router • <kbd>⌘↵</kbd> or <kbd>Ctrl+↵</kbd></>
                ) : (
                  <>Start new run • <kbd>⌘↵</kbd> or <kbd>Ctrl+↵</kbd></>
                )}
              </div>
              <div className="text-xs text-gray-500 dark:text-gray-400">
                {newMessage.length}/2000
              </div>
            </div>
          </div>
          <button
            type="submit"
            disabled={!newMessage.trim() || isSubmitting || newMessage.length > 2000}
            className={classNames(
              'btn-primary h-fit self-end flex items-center gap-2',
              (!newMessage.trim() || isSubmitting || newMessage.length > 2000) && 'opacity-50 cursor-not-allowed'
            )}
          >
            {isSubmitting ? (
              <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-current"></div>
            ) : (
              <PaperAirplaneIcon className="h-4 w-4" />
            )}
            {run?.status === 'running' ? 'Send' : 'Start'}
          </button>
        </form>
      </div>
    </div>
  )
}
