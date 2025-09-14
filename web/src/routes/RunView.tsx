import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { fetchRun } from '../api';
import { ChatThread } from '../components/ChatThread';
import { AgentSwimlanes } from '../components/AgentSwimlanes';
import { HuddleDrawer } from '../components/HuddleDrawer';
import { DecisionOverlay } from '../components/DecisionOverlay';
import { WebSearchOverlay } from '../components/WebSearchOverlay';
import { useRunStream } from '../hooks/useRunStream';
import type { RunDetails } from '../types';

export function RunView() {
  const params = useParams();
  const runId = params.id as string;
  const [run, setRun] = useState<RunDetails | null>(null);
  const [showHuddles, setShowHuddles] = useState(false);
  const [showDecisions, setShowDecisions] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const { messages, huddles, decisions, searches, agentStatus } = useRunStream(runId);

  useEffect(() => {
    fetchRun(runId).then(setRun).catch(() => setRun(null));
  }, [runId]);

  return (
    <div className="run-view">
      <header>
        <h2>Run {runId}</h2>
        <div className="actions">
          <button onClick={() => setShowDecisions(true)}>Decisions</button>
          <button onClick={() => setShowSearch(true)}>Web</button>
          <button onClick={() => setShowHuddles(true)}>Huddles</button>
        </div>
      </header>
      <main className="content">
        <ChatThread messages={messages} />
        {run && <AgentSwimlanes agents={run.agents} status={agentStatus} />}
      </main>
      <HuddleDrawer
        huddles={huddles}
        open={showHuddles}
        onClose={() => setShowHuddles(false)}
      />
      <DecisionOverlay
        decisions={decisions}
        open={showDecisions}
        onClose={() => setShowDecisions(false)}
      />
      <WebSearchOverlay
        searches={searches}
        open={showSearch}
        onClose={() => setShowSearch(false)}
      />
    </div>
  );
}
