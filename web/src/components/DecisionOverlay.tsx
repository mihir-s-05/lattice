import type { DecisionSummary } from '../types';

export function DecisionOverlay({
  decisions,
  open,
  onClose,
}: {
  decisions: DecisionSummary[];
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div role="dialog" aria-label="decision-overlay" className="overlay">
      <button onClick={onClose}>Close</button>
      {decisions.map((d) => (
        <div key={d.id} className="decision">
          <div className="topic">{d.topic}</div>
          <div className="decision-text">{d.decision}</div>
          {d.sources && (
            <ul className="sources">
              {d.sources.map((s, i) => (
                <li key={i}>{s.url ?? s.id}</li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}
