import React from 'react'
import { Routes, Route } from 'react-router-dom'
import { useHotkeys } from 'react-hotkeys-hook'
import { AppShell } from '@/components/AppShell'
import { RunView } from '@/components/RunView'
import { HotkeysProvider } from '@/components/HotkeysProvider'
import { useAppStore } from '@/store'

function App() {
  const { updatePreferences, togglePanel } = useAppStore()

  // Global keyboard shortcuts
  useHotkeys('/', (e) => {
    e.preventDefault()
    // Focus search will be handled by AppShell
  })

  useHotkeys('h', () => togglePanel('huddles'))
  useHotkeys('d', () => togglePanel('decisions'))
  useHotkeys('w', () => togglePanel('webSearch'))
  useHotkeys('g', () => togglePanel('gates'))
  useHotkeys('t', () => togglePanel('tests'))
  useHotkeys('a', () => togglePanel('artifacts'))
  useHotkeys('p', () => togglePanel('planGraph'))
  
  useHotkeys('s', () => updatePreferences({ activeView: 'swimlanes' }))
  useHotkeys('c', () => updatePreferences({ activeView: 'chat' }))

  return (
    <HotkeysProvider>
      <div className="h-full bg-white dark:bg-gray-900">
        <Routes>
          <Route path="/" element={<AppShell />}>
            <Route index element={<div className="flex items-center justify-center h-full text-gray-500">Select a run to view</div>} />
            <Route path="runs/:runId" element={<RunView />} />
          </Route>
        </Routes>
      </div>
    </HotkeysProvider>
  )
}

export default App
