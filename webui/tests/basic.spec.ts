import { test, expect } from '@playwright/test'

// Mock data for testing
const mockRuns = [
  { 
    run_id: 'test-run-001', 
    status: 'running', 
    started_at: '2024-01-15T10:00:00Z', 
    provider: 'openai', 
    model: 'gpt-4' 
  },
  { 
    run_id: 'test-run-002', 
    status: 'completed', 
    started_at: '2024-01-15T09:00:00Z', 
    provider: 'groq', 
    model: 'llama-3.1-70b' 
  }
]

const mockArtifacts = [
  {
    path: 'README.md',
    type: 'spec',
    mime_type: 'text/markdown',
    size: 1024,
    hash: 'abc123',
    tags: ['documentation'],
    created_at: '2024-01-15T10:30:00Z'
  },
  {
    path: 'src/main.py',
    type: 'code',
    mime_type: 'text/python',
    size: 2048,
    hash: 'def456',
    tags: ['python', 'main'],
    created_at: '2024-01-15T10:45:00Z'
  }
]

test.beforeEach(async ({ page }) => {
  // Mock API responses
  await page.route('**/runs', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(mockRuns)
    })
  })

  await page.route('**/runs/test-run-001', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(mockRuns[0])
    })
  })

  await page.route('**/runs/test-run-001/artifacts', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(mockArtifacts)
    })
  })

  await page.route('**/runs/test-run-001/plan_graph', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        segments: [
          { id: 'segment-1', mode: 'planning', status: 'completed', critical_path: true },
          { id: 'segment-2', mode: 'execution', status: 'active', critical_path: true },
          { id: 'segment-3', mode: 'validation', status: 'pending', critical_path: false }
        ],
        current_segment: 'segment-2',
        last_switch_reason: 'Planning phase completed successfully'
      })
    })
  })

  // Mock WebSocket - prevent connection attempts
  await page.addInitScript(() => {
    window.WebSocket = class MockWebSocket {
      constructor() {
        setTimeout(() => {
          if (this.onopen) this.onopen({})
        }, 100)
      }
      close() {}
      send() {}
    }
  })
})

test.describe('LATTICE WebUI', () => {
  test('should load the application', async ({ page }) => {
    await page.goto('/')
    
    // Should show the app title
    await expect(page.getByText('LATTICE')).toBeVisible()
    
    // Should show the search input
    await expect(page.getByPlaceholder('Search runs...')).toBeVisible()
    
    // Should show the main content area
    await expect(page.getByText('Select a run to view')).toBeVisible()
  })

  test('should display runs in sidebar', async ({ page }) => {
    await page.goto('/')
    
    // Wait for runs to load
    await expect(page.getByText('test-run-001')).toBeVisible()
    await expect(page.getByText('test-run-002')).toBeVisible()
    
    // Check run details
    await expect(page.getByText('openai • gpt-4')).toBeVisible()
    await expect(page.getByText('groq • llama-3.1-70b')).toBeVisible()
  })

  test('should navigate to run view', async ({ page }) => {
    await page.goto('/')
    
    // Click on a run
    await page.getByText('test-run-001').click()
    
    // Should navigate to the run page
    await expect(page.url()).toContain('/runs/test-run-001')
    
    // Should show run details
    await expect(page.getByText('test-run-001')).toBeVisible()
    await expect(page.getByText('openai • gpt-4')).toBeVisible()
    await expect(page.getByText('running')).toBeVisible()
  })

  test('should switch between chat and swimlanes views', async ({ page }) => {
    await page.goto('/runs/test-run-001')
    
    // Should start on chat view
    await expect(page.getByRole('tab', { name: 'Chat' })).toHaveAttribute('aria-selected', 'true')
    
    // Switch to swimlanes
    await page.getByRole('tab', { name: 'Swimlanes' }).click()
    await expect(page.getByRole('tab', { name: 'Swimlanes' })).toHaveAttribute('aria-selected', 'true')
    
    // Should show swimlanes content (even if empty)
    await expect(page.getByText('Agent Swimlanes')).toBeVisible()
  })

  test('should display inspector panels', async ({ page }) => {
    await page.goto('/runs/test-run-001')
    
    // Should show inspector
    await expect(page.getByText('Inspector')).toBeVisible()
    
    // Should show panel toggles
    await expect(page.getByText('Huddles')).toBeVisible()
    await expect(page.getByText('Decisions')).toBeVisible()
    await expect(page.getByText('Web Search')).toBeVisible()
    await expect(page.getByText('Gates')).toBeVisible()
    await expect(page.getByText('Tests')).toBeVisible()
    await expect(page.getByText('Artifacts')).toBeVisible()
    await expect(page.getByText('Plan Graph')).toBeVisible()
  })

  test('should show artifacts in inspector', async ({ page }) => {
    await page.goto('/runs/test-run-001')
    
    // Artifacts panel should be active by default
    await expect(page.getByText('📁 Artifacts')).toBeVisible()
    
    // Should show artifacts list
    await expect(page.getByText('README.md')).toBeVisible()
    await expect(page.getByText('main.py')).toBeVisible()
  })

  test('should show plan graph', async ({ page }) => {
    await page.goto('/runs/test-run-001')
    
    // Should show plan graph panel
    await expect(page.getByText('📊 Plan Graph')).toBeVisible()
    await expect(page.getByText('Plan Segments (3)')).toBeVisible()
    
    // Should show current segment info
    await expect(page.getByText('Current Segment: segment-2')).toBeVisible()
    await expect(page.getByText('Last switch: Planning phase completed successfully')).toBeVisible()
    
    // Should show segments
    await expect(page.getByText('planning')).toBeVisible()
    await expect(page.getByText('execution')).toBeVisible()
    await expect(page.getByText('validation')).toBeVisible()
  })

  test('should handle keyboard shortcuts', async ({ page }) => {
    await page.goto('/runs/test-run-001')
    
    // Test search focus (/)
    await page.keyboard.press('/')
    await expect(page.getByPlaceholder('Search runs...')).toBeFocused()
    
    // Test panel toggles
    await page.keyboard.press('Escape') // Clear focus
    await page.keyboard.press('h') // Toggle huddles
    await page.keyboard.press('d') // Toggle decisions
    await page.keyboard.press('a') // Toggle artifacts
    
    // Test view switching
    await page.keyboard.press('s') // Switch to swimlanes
    await expect(page.getByRole('tab', { name: 'Swimlanes' })).toHaveAttribute('aria-selected', 'true')
    
    await page.keyboard.press('c') // Switch to chat
    await expect(page.getByRole('tab', { name: 'Chat' })).toHaveAttribute('aria-selected', 'true')
  })

  test('should show help modal', async ({ page }) => {
    await page.goto('/')
    
    // Press ? to show help
    await page.keyboard.press('?')
    
    // Should show help modal
    await expect(page.getByText('Keyboard Shortcuts')).toBeVisible()
    await expect(page.getByText('Focus search')).toBeVisible()
    await expect(page.getByText('Toggle Huddles panel')).toBeVisible()
    
    // Close modal
    await page.getByText('Got it').click()
    await expect(page.getByText('Keyboard Shortcuts')).not.toBeVisible()
  })

  test('should handle sidebar collapse', async ({ page }) => {
    await page.goto('/')
    
    // Find and click the collapse button
    const collapseButton = page.getByTitle('Collapse sidebar')
    await collapseButton.click()
    
    // Sidebar should be collapsed
    await expect(page.getByText('LATTICE')).not.toBeVisible()
    await expect(page.getByPlaceholder('Search runs...')).not.toBeVisible()
    
    // Expand button should be visible
    const expandButton = page.getByTitle('Expand sidebar')
    await expandButton.click()
    
    // Sidebar should be expanded again
    await expect(page.getByText('LATTICE')).toBeVisible()
  })

  test('should handle inspector collapse', async ({ page }) => {
    await page.goto('/runs/test-run-001')
    
    // Find and click the collapse button
    const collapseButton = page.getByTitle('Collapse inspector')
    await collapseButton.click()
    
    // Inspector should be collapsed
    await expect(page.getByText('Inspector')).not.toBeVisible()
    
    // Expand button should be visible
    const expandButton = page.getByTitle('Expand inspector')
    await expandButton.click()
    
    // Inspector should be expanded again
    await expect(page.getByText('Inspector')).toBeVisible()
  })

  test('should show connection status', async ({ page }) => {
    await page.goto('/runs/test-run-001')
    
    // Should show live status (mocked WebSocket connection)
    await expect(page.getByText('Live')).toBeVisible()
  })

  test('should handle chat input', async ({ page }) => {
    await page.goto('/runs/test-run-001')
    
    // Should be on chat tab by default
    const chatInput = page.getByPlaceholder(/Send message to Router LLM/)
    await expect(chatInput).toBeVisible()
    
    // Type a message
    await chatInput.fill('Test message')
    await expect(chatInput).toHaveValue('Test message')
    
    // Send button should be enabled
    const sendButton = page.getByRole('button', { name: 'Send' })
    await expect(sendButton).toBeEnabled()
  })
})

test.describe('Responsive Design', () => {
  test('should work on tablet viewport', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 })
    await page.goto('/')
    
    // Should still show main components
    await expect(page.getByText('LATTICE')).toBeVisible()
    await expect(page.getByText('Select a run to view')).toBeVisible()
  })

  test('should handle mobile viewport gracefully', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 })
    await page.goto('/')
    
    // Should show the app (may be cramped but functional)
    await expect(page.getByText('LATTICE')).toBeVisible()
  })
})
