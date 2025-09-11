import React, { useState } from 'react'
import { ClipboardIcon, CheckIcon } from '@heroicons/react/24/outline'
import { MarkdownRenderer } from '../common/MarkdownRenderer'
import type { ChatMessage as ChatMessageType } from '@/types'
import classNames from 'classnames'

interface ChatMessageProps {
  message: ChatMessageType
}

export function ChatMessage({ message }: ChatMessageProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (error) {
      console.error('Failed to copy message:', error)
    }
  }

  const getRoleDisplay = (role: string) => {
    switch (role) {
      case 'router_llm':
        return { name: 'Router LLM', color: 'text-blue-600 dark:text-blue-400' }
      case 'system':
        return { name: 'System', color: 'text-gray-600 dark:text-gray-400' }
      case 'user':
        return { name: 'You', color: 'text-green-600 dark:text-green-400' }
      default:
        return { name: role, color: 'text-gray-600 dark:text-gray-400' }
    }
  }

  const roleDisplay = getRoleDisplay(message.role)

  return (
    <div className={classNames(
      'group flex gap-4 p-4 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors',
      message.role === 'user' && 'bg-primary-50 dark:bg-primary-900/20'
    )}>
      {/* Avatar */}
      <div className={classNames(
        'flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium text-white',
        message.role === 'router_llm' && 'bg-blue-600',
        message.role === 'system' && 'bg-gray-600',
        message.role === 'user' && 'bg-green-600'
      )}>
        {message.role === 'router_llm' ? 'R' : 
         message.role === 'system' ? 'S' : 
         message.role === 'user' ? 'U' : '?'}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className={classNames('font-medium text-sm', roleDisplay.color)}>
              {roleDisplay.name}
            </span>
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {new Date(message.timestamp).toLocaleTimeString()}
            </span>
          </div>
          
          <button
            onClick={handleCopy}
            className="opacity-0 group-hover:opacity-100 p-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus-ring transition-opacity"
            title="Copy message"
          >
            {copied ? (
              <CheckIcon className="h-4 w-4 text-green-500" />
            ) : (
              <ClipboardIcon className="h-4 w-4" />
            )}
          </button>
        </div>

        {/* Message Content */}
        <div className="chat-message prose prose-sm dark:prose-invert max-w-none">
          <MarkdownRenderer content={message.content} />
        </div>

        {/* Annotations */}
        {message.annotations && Object.keys(message.annotations).length > 0 && (
          <div className="mt-3 p-3 bg-gray-100 dark:bg-gray-800 rounded-md">
            <div className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-2">
              Annotations
            </div>
            <div className="space-y-1">
              {Object.entries(message.annotations).map(([key, value]) => (
                <div key={key} className="text-xs text-gray-600 dark:text-gray-400">
                  <span className="font-medium">{key}:</span>{' '}
                  <span className="font-mono">{JSON.stringify(value)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
