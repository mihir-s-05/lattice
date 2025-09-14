import type { Huddle } from '../types';

export function HuddleDrawer({
  huddles,
  open,
  onClose,
}: {
  huddles: Huddle[];
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div role="dialog" aria-label="huddle-drawer" className="drawer">
      <button onClick={onClose}>Close</button>
      {huddles.map((h) => (
        <div key={h.id} className="huddle">
          <div className="topic">{h.topic}</div>
          <div className="attendees">{h.attendees.join(', ')}</div>
          <div className="status">{h.open ? 'open' : 'closed'}</div>
        </div>
      ))}
    </div>
  );
}
