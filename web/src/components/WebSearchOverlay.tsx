import type { SearchEvent } from '../types';

export function WebSearchOverlay({
  searches,
  open,
  onClose,
}: {
  searches: SearchEvent[];
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div role="dialog" aria-label="websearch-overlay" className="overlay">
      <button onClick={onClose}>Close</button>
      {searches.map((s) => (
        <div key={s.id} className="search">
          <div className="query">{s.query}</div>
          <div className="source">{s.source}</div>
          <div className="results">{s.results_count} results</div>
        </div>
      ))}
    </div>
  );
}
