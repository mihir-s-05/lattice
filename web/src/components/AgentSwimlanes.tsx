export function AgentSwimlanes({
  agents,
  status,
}: {
  agents: string[];
  status: Record<string, string>;
}) {
  return (
    <div aria-label="swimlanes" className="swimlanes">
      {agents.map((a) => (
        <div key={a} className={`lane ${status[`agent:${a}`] ?? 'idle'}`}>
          <span className="name">{a}</span>
          <span className="state">{status[`agent:${a}`] ?? 'idle'}</span>
        </div>
      ))}
    </div>
  );
}
