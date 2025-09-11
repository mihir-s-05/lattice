import React from 'react'
import {
  UserGroupIcon,
  DocumentTextIcon,
  MagnifyingGlassIcon,
  ShieldCheckIcon,
  CheckCircleIcon,
  FolderIcon,
  ShareIcon,
} from '@heroicons/react/24/outline'
import { HuddlesPanel } from './inspector/HuddlesPanel'
import { DecisionsPanel } from './inspector/DecisionsPanel'
import { WebSearchPanel } from './inspector/WebSearchPanel'
import { GatesPanel } from './inspector/GatesPanel'
import { TestsPanel } from './inspector/TestsPanel'
import { ArtifactsPanel } from './inspector/ArtifactsPanel'
import { PlanGraphPanel } from './inspector/PlanGraphPanel'
import { useAppStore } from '@/store'
import classNames from 'classnames'

interface InspectorProps {
  collapsed: boolean
}

const PANELS = [
  {
    key: 'huddles' as const,
    title: 'Huddles',
    icon: UserGroupIcon,
    component: HuddlesPanel,
    badge: '●',
  },
  {
    key: 'decisions' as const,
    title: 'Decisions',
    icon: DocumentTextIcon,
    component: DecisionsPanel,
    badge: '◆',
  },
  {
    key: 'webSearch' as const,
    title: 'Web Search',
    icon: MagnifyingGlassIcon,
    component: WebSearchPanel,
    badge: '🔎',
  },
  {
    key: 'gates' as const,
    title: 'Gates',
    icon: ShieldCheckIcon,
    component: GatesPanel,
    badge: '▣',
  },
  {
    key: 'tests' as const,
    title: 'Tests',
    icon: CheckCircleIcon,
    component: TestsPanel,
    badge: '✓',
  },
  {
    key: 'artifacts' as const,
    title: 'Artifacts',
    icon: FolderIcon,
    component: ArtifactsPanel,
    badge: '📁',
  },
  {
    key: 'planGraph' as const,
    title: 'Plan Graph',
    icon: ShareIcon,
    component: PlanGraphPanel,
    badge: '📊',
  },
]

export function Inspector({ collapsed }: InspectorProps) {
  const { preferences, togglePanel } = useAppStore()

  if (collapsed) {
    return (
      <div className="h-full overflow-y-auto sidebar-scroll">
        <div className="p-2 space-y-2">
          {PANELS.map((panel) => {
            const Icon = panel.icon
            const isActive = preferences.panelStates[panel.key]
            
            return (
              <button
                key={panel.key}
                onClick={() => togglePanel(panel.key)}
                className={classNames(
                  'w-full p-2 rounded-md focus-ring transition-colors',
                  isActive
                    ? 'bg-primary-100 text-primary-700 dark:bg-primary-900 dark:text-primary-300'
                    : 'text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700'
                )}
                title={panel.title}
              >
                <Icon className="h-5 w-5 mx-auto" />
              </button>
            )
          })}
        </div>
      </div>
    )
  }

  const activePanels = PANELS.filter(panel => preferences.panelStates[panel.key])

  return (
    <div className="h-full flex flex-col">
      {/* Panel Toggles */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-700">
        <div className="grid grid-cols-2 gap-2">
          {PANELS.map((panel) => {
            const Icon = panel.icon
            const isActive = preferences.panelStates[panel.key]
            
            return (
              <button
                key={panel.key}
                onClick={() => togglePanel(panel.key)}
                className={classNames(
                  'flex items-center gap-2 p-2 rounded-md text-sm font-medium focus-ring transition-colors',
                  isActive
                    ? 'bg-primary-100 text-primary-700 dark:bg-primary-900 dark:text-primary-300'
                    : 'text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700'
                )}
              >
                <Icon className="h-4 w-4" />
                <span className="truncate">{panel.title}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Active Panels */}
      <div className="flex-1 overflow-y-auto sidebar-scroll">
        {activePanels.length === 0 ? (
          <div className="p-4 text-center text-gray-500 dark:text-gray-400">
            <p className="text-sm">No panels active</p>
            <p className="text-xs mt-1">Toggle panels above to view data</p>
          </div>
        ) : (
          <div className="space-y-1">
            {activePanels.map((panel, index) => {
              const Component = panel.component
              return (
                <div key={panel.key} className="border-b border-gray-200 dark:border-gray-700 last:border-b-0">
                  <div className="p-3">
                    <div className="flex items-center gap-2 mb-3">
                      <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                        {panel.badge} {panel.title}
                      </span>
                    </div>
                    <Component />
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
