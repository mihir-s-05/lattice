import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  FolderIcon,
  DocumentTextIcon,
  CodeBracketIcon,
  DocumentIcon,
  ChatBubbleLeftRightIcon,
  ClipboardDocumentListIcon,
  ArchiveBoxIcon,
  ArrowDownTrayIcon,
  EyeIcon,
  MagnifyingGlassIcon,
  ChevronDownIcon,
  ChevronRightIcon,
} from '@heroicons/react/24/outline'
import { useAppStore } from '@/store'
import { apiClient } from '@/api/client'
import { MarkdownRenderer } from '../common/MarkdownRenderer'
import type { Artifact } from '@/types'
import classNames from 'classnames'

const ARTIFACT_TYPE_ICONS = {
  code: CodeBracketIcon,
  spec: DocumentTextIcon,
  decision: ClipboardDocumentListIcon,
  huddle: ChatBubbleLeftRightIcon,
  log: DocumentIcon,
  deliverable: ArchiveBoxIcon,
}

const ARTIFACT_TYPE_COLORS = {
  code: 'text-blue-600 dark:text-blue-400',
  spec: 'text-green-600 dark:text-green-400',
  decision: 'text-purple-600 dark:text-purple-400',
  huddle: 'text-orange-600 dark:text-orange-400',
  log: 'text-gray-600 dark:text-gray-400',
  deliverable: 'text-indigo-600 dark:text-indigo-400',
}

export function ArtifactsPanel() {
  const { currentRunId } = useAppStore()
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedType, setSelectedType] = useState<string>('all')
  const [previewArtifact, setPreviewArtifact] = useState<Artifact | null>(null)
  
  const { data: artifacts = [], isLoading } = useQuery({
    queryKey: ['artifacts', currentRunId],
    queryFn: () => currentRunId ? apiClient.getRunArtifacts(currentRunId) : Promise.resolve([]),
    enabled: !!currentRunId,
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

  // Filter artifacts
  const filteredArtifacts = artifacts.filter(artifact => {
    const matchesSearch = searchQuery === '' || 
      artifact.path.toLowerCase().includes(searchQuery.toLowerCase()) ||
      artifact.tags.some(tag => tag.toLowerCase().includes(searchQuery.toLowerCase()))
    
    const matchesType = selectedType === 'all' || artifact.type === selectedType
    
    return matchesSearch && matchesType
  })

  const artifactTypes = ['all', ...Array.from(new Set(artifacts.map(a => a.type)))]

  if (artifacts.length === 0) {
    return (
      <div className="text-center py-6">
        <FolderIcon className="h-8 w-8 text-gray-400 mx-auto mb-2" />
        <p className="text-sm text-gray-500 dark:text-gray-400">No artifacts yet</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Search and Filter */}
      <div className="space-y-2">
        <div className="relative">
          <MagnifyingGlassIcon className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search artifacts..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="input-field pl-10 pr-4 py-2 text-sm w-full"
          />
        </div>
        
        <select
          value={selectedType}
          onChange={(e) => setSelectedType(e.target.value)}
          className="input-field text-sm w-full"
        >
          {artifactTypes.map(type => (
            <option key={type} value={type}>
              {type === 'all' ? 'All Types' : type.charAt(0).toUpperCase() + type.slice(1)}
            </option>
          ))}
        </select>
      </div>

      {/* Artifacts List */}
      <div className="space-y-2">
        {filteredArtifacts.length === 0 ? (
          <div className="text-center py-4 text-gray-500 dark:text-gray-400 text-sm">
            No artifacts match your filters
          </div>
        ) : (
          filteredArtifacts.map((artifact) => (
            <ArtifactCard
              key={artifact.path}
              artifact={artifact}
              onPreview={() => setPreviewArtifact(artifact)}
            />
          ))
        )}
      </div>

      {/* Preview Modal */}
      {previewArtifact && (
        <ArtifactPreviewModal
          artifact={previewArtifact}
          runId={currentRunId}
          onClose={() => setPreviewArtifact(null)}
        />
      )}
    </div>
  )
}

interface ArtifactCardProps {
  artifact: Artifact
  onPreview: () => void
}

function ArtifactCard({ artifact, onPreview }: ArtifactCardProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const { currentRunId } = useAppStore()

  const Icon = ARTIFACT_TYPE_ICONS[artifact.type] || DocumentIcon
  const iconColor = ARTIFACT_TYPE_COLORS[artifact.type] || 'text-gray-600 dark:text-gray-400'

  const handleDownload = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!currentRunId) return

    try {
      const blob = await apiClient.downloadArtifact(currentRunId, artifact.path)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = artifact.path.split('/').pop() || 'artifact'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (error) {
      console.error('Failed to download artifact:', error)
    }
  }

  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return '0 B'
    const k = 1024
    const sizes = ['B', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
  }

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full p-3 text-left hover:bg-gray-50 dark:hover:bg-gray-800/50 focus-ring transition-colors"
      >
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-3 flex-1 min-w-0">
            <Icon className={classNames('h-5 w-5 mt-0.5', iconColor)} />
            <div className="flex-1 min-w-0">
              <h5 className="font-medium text-sm text-gray-900 dark:text-gray-100 truncate">
                {artifact.path.split('/').pop()}
              </h5>
              <p className="text-xs text-gray-500 dark:text-gray-400 truncate mt-1">
                {artifact.path}
              </p>
              <div className="flex items-center gap-3 mt-1 text-xs text-gray-500 dark:text-gray-400">
                <span className={classNames('badge', `badge-${artifact.type}`)} title={artifact.type}>
                  📁 {artifact.type}
                </span>
                <span>{formatFileSize(artifact.size)}</span>
                <span>{new Date(artifact.created_at).toLocaleDateString()}</span>
              </div>
            </div>
          </div>
          
          <div className="flex items-center gap-1 ml-2">
            <button
              onClick={(e) => {
                e.stopPropagation()
                onPreview()
              }}
              className="p-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus-ring"
              title="Preview"
            >
              <EyeIcon className="h-4 w-4" />
            </button>
            <button
              onClick={handleDownload}
              className="p-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus-ring"
              title="Download"
            >
              <ArrowDownTrayIcon className="h-4 w-4" />
            </button>
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
      </button>

      {/* Expanded Content */}
      {isExpanded && (
        <div className="border-t border-gray-200 dark:border-gray-700 p-3 bg-gray-50 dark:bg-gray-800/50">
          <div className="space-y-3">
            {/* Metadata */}
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <span className="text-gray-500 dark:text-gray-400">MIME Type:</span>
                <div className="font-mono text-gray-700 dark:text-gray-300">{artifact.mime_type}</div>
              </div>
              <div>
                <span className="text-gray-500 dark:text-gray-400">Hash:</span>
                <div className="font-mono text-gray-700 dark:text-gray-300 truncate" title={artifact.hash}>
                  {artifact.hash}
                </div>
              </div>
            </div>

            {/* Tags */}
            {artifact.tags.length > 0 && (
              <div>
                <span className="text-xs text-gray-500 dark:text-gray-400 block mb-1">Tags:</span>
                <div className="flex flex-wrap gap-1">
                  {artifact.tags.map((tag) => (
                    <span
                      key={tag}
                      className="inline-flex items-center px-2 py-1 rounded-md text-xs bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-200"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-2 pt-2 border-t border-gray-200 dark:border-gray-600">
              <button
                onClick={onPreview}
                className="text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 focus-ring rounded px-2 py-1"
              >
                Preview
              </button>
              <button
                onClick={handleDownload}
                className="text-xs text-green-600 hover:text-green-700 dark:text-green-400 dark:hover:text-green-300 focus-ring rounded px-2 py-1"
              >
                Download
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

interface ArtifactPreviewModalProps {
  artifact: Artifact
  runId: string
  onClose: () => void
}

function ArtifactPreviewModal({ artifact, runId, onClose }: ArtifactPreviewModalProps) {
  const { data: content, isLoading } = useQuery({
    queryKey: ['artifact-content', runId, artifact.path],
    queryFn: () => apiClient.getArtifactContent(runId, artifact.path),
  })

  const isTextFile = artifact.mime_type.startsWith('text/') || 
    artifact.mime_type === 'application/json' ||
    artifact.mime_type === 'application/javascript' ||
    artifact.path.endsWith('.md') ||
    artifact.path.endsWith('.txt') ||
    artifact.path.endsWith('.json') ||
    artifact.path.endsWith('.js') ||
    artifact.path.endsWith('.ts') ||
    artifact.path.endsWith('.jsx') ||
    artifact.path.endsWith('.tsx')

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto">
      <div className="flex min-h-full items-center justify-center p-4">
        <div className="fixed inset-0 bg-black bg-opacity-25" onClick={onClose} />
        
        <div className="relative w-full max-w-4xl bg-white dark:bg-gray-800 rounded-2xl shadow-xl">
          {/* Header */}
          <div className="flex items-center justify-between p-6 border-b border-gray-200 dark:border-gray-700">
            <div>
              <h3 className="text-lg font-medium text-gray-900 dark:text-gray-100">
                {artifact.path.split('/').pop()}
              </h3>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                {artifact.path}
              </p>
            </div>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus-ring rounded"
            >
              <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* Content */}
          <div className="p-6">
            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
              </div>
            ) : isTextFile && content ? (
              <div className="max-h-96 overflow-y-auto">
                {artifact.path.endsWith('.md') ? (
                  <div className="prose prose-sm dark:prose-invert max-w-none">
                    <MarkdownRenderer content={content} />
                  </div>
                ) : (
                  <pre className="text-sm bg-gray-100 dark:bg-gray-900 p-4 rounded-lg overflow-x-auto">
                    <code>{content}</code>
                  </pre>
                )}
              </div>
            ) : (
              <div className="text-center py-12 text-gray-500 dark:text-gray-400">
                <DocumentIcon className="h-12 w-12 mx-auto mb-4" />
                <p>Preview not available for this file type</p>
                <p className="text-sm mt-1">MIME type: {artifact.mime_type}</p>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex justify-end gap-3 px-6 py-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50">
            <button
              onClick={onClose}
              className="btn-secondary"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
