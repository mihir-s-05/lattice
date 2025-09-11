import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Simple dev-time mock API plugin
const mockApiPlugin = () => {
  return {
    name: 'mock-api',
    apply: 'serve' as const,
    configureServer(server: any) {
      const runs = [
        {
          run_id: 'test-run-001',
          status: 'running',
          started_at: new Date().toISOString(),
          provider: 'openai',
          model: 'gpt-4',
        },
        {
          run_id: 'test-run-002',
          status: 'completed',
          started_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
          provider: 'groq',
          model: 'llama-3.1-70b',
        },
      ]

      server.middlewares.use((req: any, res: any, next: any) => {
        const url: string = req.url || ''
        const method: string = (req.method || 'GET').toUpperCase()

        if (!url.startsWith('/runs') || method !== 'GET') {
          return next()
        }

        // /runs
        if (url === '/runs' || url.startsWith('/runs?')) {
          res.statusCode = 200
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(runs))
          return
        }

        // /runs/:runId[/...]
        const match = url.match(/^\/runs\/([^/]+)(?:\/(.*))?$/)
        if (!match) return next()

        const runId = decodeURIComponent(match[1])
        const tail = match[2]
        const run = runs.find(r => r.run_id === runId) || runs[0]

        if (!tail) {
          res.statusCode = 200
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(run))
          return
        }

        if (tail === 'artifacts') {
          const artifacts = [
            {
              path: 'README.md',
              type: 'spec',
              mime_type: 'text/markdown',
              size: 1024,
              hash: 'abc123',
              tags: ['documentation'],
              created_at: new Date().toISOString(),
            },
            {
              path: 'src/main.py',
              type: 'code',
              mime_type: 'text/x-python',
              size: 2048,
              hash: 'def456',
              tags: ['python', 'main'],
              created_at: new Date().toISOString(),
            },
          ]
          res.statusCode = 200
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(artifacts))
          return
        }

        if (tail.startsWith('artifacts/')) {
          const content = `# Mock Artifact\n\nThis is mock content for ${decodeURIComponent(tail.slice('artifacts/'.length))}.`
          res.statusCode = 200
          res.setHeader('Content-Type', 'text/plain; charset=utf-8')
          res.end(content)
          return
        }

        if (tail === 'plan_graph') {
          const plan = {
            segments: [
              { id: 'segment-1', mode: 'planning', status: 'completed', critical_path: true },
              { id: 'segment-2', mode: 'execution', status: 'active', critical_path: true },
              { id: 'segment-3', mode: 'validation', status: 'pending', critical_path: false },
            ],
            current_segment: 'segment-2',
            last_switch_reason: 'Planning phase completed successfully',
          }
          res.statusCode = 200
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(plan))
          return
        }

        return next()
      })
    },
  }
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const enableMock = env.VITE_ENABLE_DEV_MOCK === 'true'

  return {
    plugins: [react(), ...(enableMock ? [mockApiPlugin()] : [])],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: 3000,
      host: true,
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
    },
  }
})
