import { TransactionEvent } from "../lib/api";

function statusClass(status: string) {
  if (status === "success" || status === "ok") return "ok";
  if (status === "failed") return "danger";
  if (status === "retry" || status === "fallback") return "warn";
  return "info";
}

export function LiveTransactions({
  events,
  connected,
}: {
  events: TransactionEvent[];
  connected: boolean;
}) {
  const newest = [...events].reverse().slice(0, 40);
  return (
    <div className="panel">
      <div className="panel-title" style={{ display: "flex", justifyContent: "space-between" }}>
        <span>Live transactions</span>
        <span className={`badge ${connected ? "ok" : "warn"}`}>
          <span className="live-dot" style={{ background: connected ? "var(--accent)" : "var(--warn)" }} />
          {connected ? "stream live" : "reconnecting"}
        </span>
      </div>
      <div className="scroll-y-tall">
        {newest.length === 0 && <div className="empty">Waiting for pipeline activity…</div>}
        {newest.map((e) => (
          <div className="tx-row" key={e.id}>
            <div>
              <span className={`badge ${statusClass(e.status)}`}>{e.kind}</span>
            </div>
            <div>
              <div style={{ fontWeight: 500 }}>{e.title}</div>
              {e.detail && (
                <div style={{ color: "var(--muted)", fontSize: "0.78rem", marginTop: 2 }}>{e.detail}</div>
              )}
              {e.provider && (
                <div style={{ color: "var(--muted)", fontSize: "0.72rem", marginTop: 2 }}>
                  provider · {e.provider}
                </div>
              )}
            </div>
            <div style={{ color: "var(--muted)", fontSize: "0.72rem", textAlign: "right" }}>
              {e.latency_ms != null ? `${Math.round(e.latency_ms)}ms` : ""}
              <div>{new Date(e.created_at).toLocaleTimeString()}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
