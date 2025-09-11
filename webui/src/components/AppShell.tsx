import React, { useState, useRef } from 'react'
import { Outlet } from 'react-router-dom'
import { useHotkeys } from 'react-hotkeys-hook'
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  Bars3Icon,
  MagnifyingGlassIcon,
} from '@heroicons/react/24/outline'
import { RunSidebar } from './RunSidebar'
import { Inspector } from './Inspector'
import { useAppStore } from '@/store'
import classNames from 'classnames'

export function AppShell() {
  const searchInputRef = useRef<HTMLInputElement>(null)
  const { preferences, updatePreferences } = useAppStore()
  const [searchQuery, setSearchQuery] = useState('')

  // Focus search shortcut
  useHotkeys('/', (e) => {
    e.preventDefault()
    searchInputRef.current?.focus()
  }, { enableOnFormTags: false })

  const toggleSidebar = () => {
    updatePreferences({ sidebarCollapsed: !preferences.sidebarCollapsed })
  }

  const toggleInspector = () => {
    updatePreferences({ inspectorCollapsed: !preferences.inspectorCollapsed })
  }

  return (
    <div className="flex h-full">
      {/* Left Sidebar */}
      <div
        className={classNames(
          'bg-gray-50 dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 transition-all duration-300',
          preferences.sidebarCollapsed ? 'w-16' : 'w-80'
        )}
      >
        <div className="flex h-full flex-col">
          {/* Sidebar Header */}
          <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700">
            {!preferences.sidebarCollapsed && (
              <>
                <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
                  LATTICE
                </h1>
                <div className="flex items-center gap-2">
                  <div className="relative">
                    <MagnifyingGlassIcon className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-gray-400" />
                    <input
                      ref={searchInputRef}
                      type="text"
                      placeholder="Search runs..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      className="input-field pl-10 pr-4 py-2 text-sm w-48"
                    />
                  </div>
                </div>
              </>
            )}
            <button
              onClick={toggleSidebar}
              className="p-1 rounded-md text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus-ring"
              title={preferences.sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {preferences.sidebarCollapsed ? (
                <ChevronRightIcon className="h-5 w-5" />
              ) : (
                <ChevronLeftIcon className="h-5 w-5" />
              )}
            </button>
          </div>

          {/* Sidebar Content */}
          <div className="flex-1 overflow-hidden">
            <RunSidebar
              collapsed={preferences.sidebarCollapsed}
              searchQuery={searchQuery}
            />
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Main Workspace */}
        <div className="flex-1 flex min-h-0">
          <div className="flex-1 flex flex-col">
            <Outlet />
          </div>

          {/* Right Inspector */}
          <div
            className={classNames(
              'inspector-panel bg-gray-50 dark:bg-gray-800 transition-all duration-300',
              preferences.inspectorCollapsed ? 'w-12' : 'w-96'
            )}
          >
            <div className="flex h-full flex-col">
              {/* Inspector Header */}
              <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700">
                {!preferences.inspectorCollapsed && (
                  <h2 className="text-lg font-medium text-gray-900 dark:text-gray-100">
                    Inspector
                  </h2>
                )}
                <button
                  onClick={toggleInspector}
                  className="p-1 rounded-md text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus-ring"
                  title={preferences.inspectorCollapsed ? 'Expand inspector' : 'Collapse inspector'}
                >
                  {preferences.inspectorCollapsed ? (
                    <ChevronLeftIcon className="h-5 w-5" />
                  ) : (
                    <ChevronRightIcon className="h-5 w-5" />
                  )}
                </button>
              </div>

              {/* Inspector Content */}
              <div className="flex-1 overflow-hidden">
                <Inspector collapsed={preferences.inspectorCollapsed} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
