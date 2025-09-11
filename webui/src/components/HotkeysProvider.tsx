import React, { createContext, useContext, useState } from 'react'
import { Dialog, Transition } from '@headlessui/react'
import { Fragment } from 'react'
import { XMarkIcon } from '@heroicons/react/24/outline'

interface HotkeysContextType {
  showHelp: () => void
  hideHelp: () => void
}

const HotkeysContext = createContext<HotkeysContextType | null>(null)

export function useHotkeysHelp() {
  const context = useContext(HotkeysContext)
  if (!context) {
    throw new Error('useHotkeysHelp must be used within HotkeysProvider')
  }
  return context
}

const SHORTCUTS = [
  { key: '/', description: 'Focus search' },
  { key: 'H', description: 'Toggle Huddles panel' },
  { key: 'D', description: 'Toggle Decisions panel' },
  { key: 'W', description: 'Toggle Web Search panel' },
  { key: 'G', description: 'Toggle Gates panel' },
  { key: 'T', description: 'Toggle Tests panel' },
  { key: 'A', description: 'Toggle Artifacts panel' },
  { key: 'P', description: 'Toggle Plan Graph panel' },
  { key: 'S', description: 'Switch to Swimlanes view' },
  { key: 'C', description: 'Switch to Chat view' },
  { key: '?', description: 'Show this help' },
]

interface HotkeysProviderProps {
  children: React.ReactNode
}

export function HotkeysProvider({ children }: HotkeysProviderProps) {
  const [isOpen, setIsOpen] = useState(false)

  const showHelp = () => setIsOpen(true)
  const hideHelp = () => setIsOpen(false)

  // Global shortcut for help
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const target = e.target as HTMLElement
        // Don't trigger if user is typing in an input
        if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.contentEditable === 'true') {
          return
        }
        e.preventDefault()
        showHelp()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  return (
    <HotkeysContext.Provider value={{ showHelp, hideHelp }}>
      {children}
      
      <Transition appear show={isOpen} as={Fragment}>
        <Dialog as="div" className="relative z-50" onClose={hideHelp}>
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
                <Dialog.Panel className="w-full max-w-md transform overflow-hidden rounded-2xl bg-white dark:bg-gray-800 p-6 text-left align-middle shadow-xl transition-all">
                  <div className="flex items-center justify-between mb-4">
                    <Dialog.Title as="h3" className="text-lg font-medium leading-6 text-gray-900 dark:text-gray-100">
                      Keyboard Shortcuts
                    </Dialog.Title>
                    <button
                      onClick={hideHelp}
                      className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus-ring rounded"
                    >
                      <XMarkIcon className="h-5 w-5" />
                    </button>
                  </div>

                  <div className="space-y-3">
                    {SHORTCUTS.map((shortcut) => (
                      <div key={shortcut.key} className="flex items-center justify-between">
                        <span className="text-sm text-gray-600 dark:text-gray-300">
                          {shortcut.description}
                        </span>
                        <kbd className="inline-flex items-center px-2 py-1 rounded bg-gray-100 dark:bg-gray-700 text-xs font-mono text-gray-600 dark:text-gray-300">
                          {shortcut.key}
                        </kbd>
                      </div>
                    ))}
                  </div>

                  <div className="mt-6">
                    <button
                      type="button"
                      className="btn-primary w-full"
                      onClick={hideHelp}
                    >
                      Got it
                    </button>
                  </div>
                </Dialog.Panel>
              </Transition.Child>
            </div>
          </div>
        </Dialog>
      </Transition>
    </HotkeysContext.Provider>
  )
}
