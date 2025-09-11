import type { Run, Artifact, PlanGraph } from '@/types'

// Prefer configured API base; fall back to same-origin in dev when backend is not reachable
const API_BASE: string | undefined = (import.meta as any).env?.VITE_API_BASE

class ApiClient {
  private async request<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const tryJson = async (url: string) => {
      const response = await fetch(url, {
        headers: {
          'Content-Type': 'application/json',
          ...options?.headers,
        },
        ...options,
      })
      if (!response.ok) {
        throw new Error(`API request failed: ${response.status} ${response.statusText}`)
      }
      return response.json()
    }

    // First try configured base URL (if provided)
    if (API_BASE && API_BASE.trim() !== '') {
      try {
        return await tryJson(`${API_BASE}${endpoint}`)
      } catch (err) {
        // If we're in dev, fall back to same-origin relative path (works with Vite mock API)
        if (import.meta.env.DEV) {
          try {
            return await tryJson(endpoint)
          } catch (_) {
            // Re-throw original error for better diagnostics
            throw err
          }
        }
        throw err
      }
    }

    // No API_BASE configured: use same-origin
    return tryJson(endpoint)
  }

  async getRuns(): Promise<Run[]> {
    return this.request<Run[]>('/runs')
  }

  async getRun(runId: string): Promise<Run> {
    return this.request<Run>(`/runs/${runId}`)
  }

  async getRunArtifacts(runId: string): Promise<Artifact[]> {
    return this.request<Artifact[]>(`/runs/${runId}/artifacts`)
  }

  async getArtifactContent(runId: string, path: string): Promise<string> {
    const rel = `/runs/${runId}/artifacts/${encodeURIComponent(path)}`
    const tryText = async (url: string) => {
      const response = await fetch(url)
      if (!response.ok) {
        throw new Error(`Failed to fetch artifact: ${response.status} ${response.statusText}`)
      }
      return response.text()
    }
    if (API_BASE && API_BASE.trim() !== '') {
      try {
        return await tryText(`${API_BASE}${rel}`)
      } catch (err) {
        if (import.meta.env.DEV) {
          try {
            return await tryText(rel)
          } catch (_) {
            throw err
          }
        }
        throw err
      }
    }
    return tryText(rel)
  }

  async downloadArtifact(runId: string, path: string): Promise<Blob> {
    const rel = `/runs/${runId}/artifacts/${encodeURIComponent(path)}`
    const tryBlob = async (url: string) => {
      const response = await fetch(url)
      if (!response.ok) {
        throw new Error(`Failed to download artifact: ${response.status} ${response.statusText}`)
      }
      return response.blob()
    }
    if (API_BASE && API_BASE.trim() !== '') {
      try {
        return await tryBlob(`${API_BASE}${rel}`)
      } catch (err) {
        if (import.meta.env.DEV) {
          try {
            return await tryBlob(rel)
          } catch (_) {
            throw err
          }
        }
        throw err
      }
    }
    return tryBlob(rel)
  }

  async getPlanGraph(runId: string): Promise<PlanGraph> {
    return this.request<PlanGraph>(`/runs/${runId}/plan_graph`)
  }

  async startRun(prompt: string, options?: Record<string, any>): Promise<{ run_id: string }> {
    return this.request<{ run_id: string }>('/runs', {
      method: 'POST',
      body: JSON.stringify({ prompt, options }),
    })
  }

  async abortRun(runId: string): Promise<void> {
    await this.request(`/runs/${runId}/abort`, {
      method: 'POST',
    })
  }
}

export const apiClient = new ApiClient()
