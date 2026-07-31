import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, CallRecord, Message } from "../lib/api";

export function CallsPage() {
  const [calls, setCalls] = useState<CallRecord[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      setCalls(await api.calls.list());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    if (!selected) {
      setMessages([]);
      return;
    }
    void api.calls
      .messages(selected)
      .then(setMessages)
      .catch((err) => setError(String(err)));
  }, [selected]);

  return (
    <div className="page">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "end", marginBottom: "1rem" }}>
        <div>
          <h2>Call records</h2>
          <p style={{ color: "var(--muted)", margin: "0.35rem 0 0" }}>History of voice and chat sessions</p>
        </div>
        <button className="btn" type="button" onClick={() => void refresh()}>
          Refresh
        </button>
      </div>

      {error && <div className="badge danger">{error}</div>}

      <div className="grid-2">
        <div className="panel">
          <div className="panel-title">Sessions</div>
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Channel</th>
                <th>Status</th>
                <th>Faults</th>
                <th>Preview</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => setSelected(c.id)}
                  style={{ cursor: "pointer", background: selected === c.id ? "var(--accent-dim)" : undefined }}
                >
                  <td>{c.id}</td>
                  <td>{c.channel}</td>
                  <td>
                    <span className={`badge ${c.status === "active" ? "ok" : c.status === "failed" ? "danger" : "info"}`}>
                      {c.status}
                    </span>
                  </td>
                  <td>{c.fault_events}</td>
                  <td style={{ color: "var(--muted)", maxWidth: 220 }}>{c.transcript_preview || c.title}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {calls.length === 0 && <div className="empty">No calls yet — start one from the console.</div>}
        </div>

        <div className="panel">
          <div className="panel-title">Transcript {selected ? `#${selected}` : ""}</div>
          <div className="scroll-y-tall">
            {!selected && <div className="empty">Select a call</div>}
            {messages.map((m) => (
              <div key={m.id} className={`msg ${m.role}`}>
                <div style={{ fontSize: "0.7rem", color: "var(--muted)", marginBottom: 4 }}>
                  {m.role} · {new Date(m.created_at).toLocaleString()}
                </div>
                {m.content}
              </div>
            ))}
          </div>
          {selected && (
            <div className="row-actions" style={{ marginTop: "0.75rem" }}>
              <Link className="btn" to="/">
                Back to console
              </Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
