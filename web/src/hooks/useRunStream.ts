import { useEffect, useState } from 'react';
import { connectEvents } from '../api';
import type {
  EventEnvelope,
  Message,
  Huddle,
  DecisionSummary,
  SearchEvent,
  ChatPayload,
  HuddlePayload,
  DecisionPayload,
  SearchPayload,
  ToolPayload,
} from '../types';

export interface RunStreamState {
  messages: Message[];
  huddles: Huddle[];
  decisions: DecisionSummary[];
  searches: SearchEvent[];
  agentStatus: Record<string, string>;
}

const initialState: RunStreamState = {
  messages: [],
  huddles: [],
  decisions: [],
  searches: [],
  agentStatus: {},
};

export function reduceState(state: RunStreamState, ev: EventEnvelope): RunStreamState {
  switch (ev.kind) {
    case 'chat': {
      const p = ev.payload as ChatPayload;
      const existing = state.messages.find((m) => m.id === p.message_id);
      if (existing) {
        existing.text = p.text;
        existing.annotations = p.annotations;
        return { ...state, messages: [...state.messages] };
      }
      const msg: Message = {
        id: p.message_id,
        role: ev.role,
        text: p.text,
        annotations: p.annotations,
      };
      return { ...state, messages: [...state.messages, msg] };
    }
    case 'huddle': {
      const p = ev.payload as HuddlePayload;
      if (p.event === 'huddle_opened') {
        const h: Huddle = {
          id: p.huddle_id,
          topic: p.topic,
          attendees: p.attendees,
          open: true,
        };
        return { ...state, huddles: [...state.huddles, h] };
      }
      return {
        ...state,
        huddles: state.huddles.map((h) =>
          h.id === p.huddle_id ? { ...h, open: false } : h
        ),
      };
    }
    case 'decision': {
      const p = ev.payload as DecisionPayload;
      return { ...state, decisions: [...state.decisions, p.summary] };
    }
    case 'search': {
      const p = ev.payload as SearchPayload;
      const s: SearchEvent = {
        id: `${state.searches.length}`,
        source: p.source,
        query: p.query,
        results_count: p.results_count,
      };
      return { ...state, searches: [...state.searches, s] };
    }
    case 'tool': {
      const p = ev.payload as ToolPayload;
      if (ev.role.startsWith('agent:')) {
        const status =
          p.status === 'started'
            ? 'acting'
            : p.status === 'failed'
            ? 'error'
            : 'idle';
        return {
          ...state,
          agentStatus: { ...state.agentStatus, [ev.role]: status },
        };
      }
      return state;
    }
    default:
      return state;
  }
}

export function useRunStream(
  runId: string,
  options?: { eventSource?: (onEvent: (ev: EventEnvelope) => void) => () => void }
) {
  const [state, setState] = useState<RunStreamState>(initialState);

  const handleEvent = (ev: EventEnvelope) => {
    setState((s) => reduceState(s, ev));
  };

  useEffect(() => {
    if (options?.eventSource) {
      return options.eventSource(handleEvent);
    }
    return connectEvents(runId, handleEvent);
  }, [runId]);

  return { ...state, addEvent: handleEvent };
}
