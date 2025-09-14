import type { RunDetails, RunMeta, EventEnvelope } from './types';

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

export async function fetchRuns(): Promise<RunMeta[]> {
  const res = await fetch(`${API_BASE}/runs`);
  if (!res.ok) throw new Error('failed to load runs');
  return res.json();
}

export async function fetchRun(id: string): Promise<RunDetails> {
  const res = await fetch(`${API_BASE}/runs/${id}`);
  if (!res.ok) throw new Error('failed to load run');
  return res.json();
}

export function connectEvents(id: string, onEvent: (ev: EventEnvelope) => void): () => void {
  const es = new EventSource(`${API_BASE}/runs/${id}/events`);
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data) as EventEnvelope;
      onEvent(data);
    } catch (err) {
      console.error('bad event', err);
    }
  };
  return () => es.close();
}
