import { render, screen, fireEvent } from '@testing-library/react';
import { ChatThread } from '../components/ChatThread';
import { DecisionOverlay } from '../components/DecisionOverlay';
import { HuddleDrawer } from '../components/HuddleDrawer';
import { RunView } from '../routes/RunView';
import { reduceState, RunStreamState } from '../hooks/useRunStream';
import type { EventEnvelope } from '../types';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

// ---- Streaming chat test ----
test('renders streaming chat messages', () => {
  let state: RunStreamState = {
    messages: [],
    huddles: [],
    decisions: [],
    searches: [],
    agentStatus: {},
  };
  const partial: EventEnvelope = {
    ts: '',
    kind: 'chat',
    role: 'router_llm',
    run_id: '1',
    payload: { message_id: 'm1', text: 'hel', is_partial: true },
  } as any;
  state = reduceState(state, partial);
  const { rerender } = render(<ChatThread messages={state.messages} />);
  expect(screen.getByText('hel')).toBeInTheDocument();
  const final: EventEnvelope = {
    ts: '',
    kind: 'chat',
    role: 'router_llm',
    run_id: '1',
    payload: { message_id: 'm1', text: 'hello', is_partial: false },
  } as any;
  state = reduceState(state, final);
  rerender(<ChatThread messages={state.messages} />);
  expect(screen.getByText('hello')).toBeInTheDocument();
});

// ---- Overlay toggles ----
vi.mock('../api', () => ({
  fetchRun: () =>
    Promise.resolve({
      run_id: '1',
      status: 'running',
      agents: [],
      current_mode: 'weave',
      artifacts_root: '',
      has_web_search: true,
    }),
}));

vi.mock('../hooks/useRunStream', async () => {
  const actual = await vi.importActual<typeof import('../hooks/useRunStream')>(
    '../hooks/useRunStream'
  );
  return {
    ...actual,
    useRunStream: () => ({
      messages: [],
      huddles: [],
      decisions: [
        { id: 'd1', topic: 't', decision: 'd', sources: [] },
      ],
      searches: [
        { id: 's1', source: 'groq', query: 'q', results_count: 1 },
      ],
      agentStatus: {},
    }),
  };
});

test('overlay toggles show panels', async () => {
  render(
    <MemoryRouter initialEntries={['/runs/1']}>
      <Routes>
        <Route path="/runs/:id" element={<RunView />} />
      </Routes>
    </MemoryRouter>
  );
  // decisions overlay
  fireEvent.click(screen.getByText('Decisions'));
  expect(await screen.findByLabelText('decision-overlay')).toBeInTheDocument();
  // web overlay
  fireEvent.click(screen.getByText('Web'));
  expect(await screen.findByLabelText('websearch-overlay')).toBeInTheDocument();
});

// ---- Huddle lifecycle ----
test('huddle open and close updates drawer', () => {
  let state: RunStreamState = {
    messages: [],
    huddles: [],
    decisions: [],
    searches: [],
    agentStatus: {},
  };
  const openEv: EventEnvelope = {
    ts: '',
    kind: 'huddle',
    role: 'system',
    run_id: '1',
    payload: {
      event: 'huddle_opened',
      huddle_id: 'h1',
      topic: 'Discuss',
      attendees: ['a'],
    },
  } as any;
  state = reduceState(state, openEv);
  const { rerender } = render(
    <HuddleDrawer huddles={state.huddles} open={true} onClose={() => {}} />
  );
  expect(screen.getByText('open')).toBeInTheDocument();
  const closeEv: EventEnvelope = {
    ts: '',
    kind: 'huddle',
    role: 'system',
    run_id: '1',
    payload: {
      event: 'huddle_closed',
      huddle_id: 'h1',
      topic: 'Discuss',
      attendees: ['a'],
    },
  } as any;
  state = reduceState(state, closeEv);
  rerender(<HuddleDrawer huddles={state.huddles} open={true} onClose={() => {}} />);
  expect(screen.getByText('closed')).toBeInTheDocument();
});

// ---- Decision sources rendering ----
test('decision overlay renders sources', () => {
  const decisions = [
    {
      id: 'd1',
      topic: 't1',
      decision: 'd1',
      sources: [
        { type: 'external', url: 'https://example.com' },
        { type: 'artifact', id: 'file.txt' },
      ],
    },
  ];
  render(
    <DecisionOverlay decisions={decisions} open={true} onClose={() => {}} />
  );
  expect(screen.getByText('https://example.com')).toBeInTheDocument();
  expect(screen.getByText('file.txt')).toBeInTheDocument();
});
