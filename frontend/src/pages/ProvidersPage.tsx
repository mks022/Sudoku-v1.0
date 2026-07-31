import { FormEvent, useEffect, useState } from "react";
import { api, ProviderConfig, ProviderKind } from "../lib/api";

const KINDS: ProviderKind[] = ["llm", "stt", "tts", "vad", "mcp"];

const emptyForm = {
  kind: "llm" as ProviderKind,
  provider: "openai",
  api_key: "",
  base_url: "",
  model: "",
  enabled: true,
  is_fallback: false,
  priority: 0,
};

export function ProvidersPage() {
  const [rows, setRows] = useState<ProviderConfig[]>([]);
  const [editing, setEditing] = useState<ProviderConfig | null>(null);
  const [form, setForm] = useState({ ...emptyForm });
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  async function refresh() {
    setRows(await api.providers.list());
  }

  useEffect(() => {
    void refresh().catch((err) => setError(String(err)));
  }, []);

  function startEdit(row: ProviderConfig) {
    setEditing(row);
    setForm({
      kind: row.kind,
      provider: row.provider,
      api_key: "",
      base_url: row.base_url,
      model: row.model,
      enabled: row.enabled,
      is_fallback: row.is_fallback,
      priority: row.priority,
    });
    setSaved("");
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaved("");
    try {
      const body = {
        ...form,
        extra: editing?.extra || {},
      };
      if (editing) {
        await api.providers.update(editing.id, body);
      } else {
        await api.providers.create(body);
      }
      setEditing(null);
      setForm({ ...emptyForm });
      setSaved("Provider saved. Secrets stay on the API host.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove(id: number) {
    await api.providers.remove(id);
    await refresh();
  }

  return (
    <div className="page">
      <div style={{ marginBottom: "1rem" }}>
        <h2>Providers & API secrets</h2>
        <p style={{ color: "var(--muted)", margin: "0.35rem 0 0" }}>
          Configure LLM, STT, TTS, VAD, and MCP endpoints. Primary + fallback slots power circuit-breaker failover.
        </p>
      </div>

      <div className="grid-2">
        <div className="panel">
          <div className="panel-title">Configured providers</div>
          <table className="table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Provider</th>
                <th>Model</th>
                <th>Key</th>
                <th>Flags</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td>
                    <span className="badge info">{r.kind}</span>
                  </td>
                  <td>{r.provider}</td>
                  <td style={{ color: "var(--muted)" }}>{r.model || "—"}</td>
                  <td style={{ fontSize: "0.75rem" }}>{r.api_key_masked || "unset"}</td>
                  <td>
                    {r.enabled ? <span className="badge ok">on</span> : <span className="badge">off</span>}{" "}
                    {r.is_fallback && <span className="badge warn">fallback</span>}
                  </td>
                  <td>
                    <div className="row-actions">
                      <button className="btn btn-ghost" type="button" onClick={() => startEdit(r)}>
                        Edit
                      </button>
                      <button className="btn btn-ghost" type="button" onClick={() => void remove(r.id)}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <div className="empty">No providers seeded yet.</div>}
        </div>

        <div className="panel">
          <div className="panel-title">{editing ? `Edit #${editing.id}` : "Add provider"}</div>
          <form onSubmit={onSubmit}>
            <div className="field">
              <label>Kind</label>
              <select
                value={form.kind}
                onChange={(e) => setForm({ ...form, kind: e.target.value as ProviderKind })}
              >
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Provider</label>
              <input
                value={form.provider}
                onChange={(e) => setForm({ ...form, provider: e.target.value })}
                placeholder="openai / deepgram / cartesia / silero"
                required
              />
            </div>
            <div className="field">
              <label>API secret {editing ? `(leave blank to keep ${editing.api_key_masked || "existing"})` : ""}</label>
              <input
                type="password"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                placeholder="sk-… / Token…"
                autoComplete="off"
              />
            </div>
            <div className="field">
              <label>Base URL</label>
              <input
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                placeholder="https://api.openai.com/v1"
              />
            </div>
            <div className="field">
              <label>Model</label>
              <input
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
                placeholder="gpt-4o-mini / nova-2 / sonic-english"
              />
            </div>
            <div className="field">
              <label>Priority (lower = preferred)</label>
              <input
                type="number"
                value={form.priority}
                onChange={(e) => setForm({ ...form, priority: Number(e.target.value) })}
              />
            </div>
            <label className="switch">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              />
              Enabled
            </label>
            <label className="switch" style={{ marginLeft: "1rem" }}>
              <input
                type="checkbox"
                checked={form.is_fallback}
                onChange={(e) => setForm({ ...form, is_fallback: e.target.checked })}
              />
              Fallback
            </label>
            <div className="row-actions" style={{ marginTop: "1rem" }}>
              <button className="btn btn-primary" type="submit">
                Save
              </button>
              {editing && (
                <button
                  className="btn btn-ghost"
                  type="button"
                  onClick={() => {
                    setEditing(null);
                    setForm({ ...emptyForm });
                  }}
                >
                  Cancel
                </button>
              )}
            </div>
            {saved && (
              <div className="badge ok" style={{ marginTop: 12 }}>
                {saved}
              </div>
            )}
            {error && (
              <div className="badge danger" style={{ marginTop: 12 }}>
                {error}
              </div>
            )}
          </form>
        </div>
      </div>
    </div>
  );
}
