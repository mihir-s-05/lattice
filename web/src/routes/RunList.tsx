import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchRuns } from '../api';
import type { RunMeta } from '../types';

export function RunList() {
  const [runs, setRuns] = useState<RunMeta[]>([]);
  useEffect(() => {
    fetchRuns().then(setRuns).catch(() => setRuns([]));
  }, []);
  return (
    <div>
      <h1>Runs</h1>
      <ul>
        {runs.map((r) => (
          <li key={r.run_id}>
            <Link to={`/runs/${r.run_id}`}>{r.run_id}</Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
