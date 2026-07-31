import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { ConsoleChat } from "../components/ConsoleChat";
import { LiveTransactions } from "../components/LiveTransactions";
import { useLiveTransactions } from "../hooks/useLiveTransactions";

export function ConsolePage() {
  const { events, connected } = useLiveTransactions();
  const [health, setHealth] = useState<string>("…");
  const [network, setNetwork] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    void api
      .health()
      .then((h) => setHealth(h.ok ? `online · ${h.rag_docs} RAG docs` : "degraded"))
      .catch(() => setHealth("api unreachable"));
    void api
      .mcp()
      .then((m) => setNetwork(m.network_state))
      .catch(() => setNetwork(null));
  }, [events.length]);

  const peers = (network?.peers || {}) as Record<string, { status: string; loss_pct: number; latency_ms: number }>;

  return (
    <div className="page">
      <section className="hero-console">
        <div className="badge ok" style={{ marginBottom: "1rem" }}>
          <span className="live-dot" />
          {health}
        </div>
        <h1>NetGuard</h1>
        <p>
          Pipecat voice pipeline with STT, TTS, LLM, Silero VAD, and MCP mitigation tools — resilient to
          provider and network faults.
        </p>
        <div className="row-actions">
          <a className="btn btn-primary" href="#channel">
            Open channel
          </a>
          <a className="btn" href="/providers">
            Configure providers
          </a>
        </div>
      </section>

      <div className="grid-2" id="channel">
        <ConsoleChat />
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <LiveTransactions events={events} connected={connected} />
          <div className="panel">
            <div className="panel-title">Network plane</div>
            {Object.keys(peers).length === 0 && <div className="empty">No telemetry yet</div>}
            {Object.entries(peers).map(([name, p]) => (
              <div
                key={name}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  padding: "0.45rem 0",
                  borderBottom: "1px solid var(--line)",
                }}
              >
                <span>{name}</span>
                <span className={`badge ${p.status === "up" ? "ok" : p.status === "degraded" ? "warn" : "danger"}`}>
                  {p.status} · {p.loss_pct}% · {p.latency_ms}ms
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
