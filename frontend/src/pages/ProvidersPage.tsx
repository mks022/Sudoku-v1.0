import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, ProviderCatalog, ProviderConfig } from "../lib/api";

type FormState = {
  kind: string;
  provider: string;
  api_key: string;
  clear_api_key: boolean;
  base_url: string;
  model: string;
  protocol: string;
  auth_scheme: string;
  auth_header: string;
  path: string;
  response_path: string;
  voice: string;
  headers_json: string;
  body_template_json: string;
  enabled: boolean;
  is_fallback: boolean;
  priority: number;
};

const emptyForm = (): FormState => ({
  kind: "llm",
  provider: "",
  api_key: "",
  clear_api_key: false,
  base_url: "",
  model: "",
  protocol: "openai_chat",
  auth_scheme: "bearer",
  auth_header: "",
  path: "",
  response_path: "",
  voice: "",
  headers_json: "{}",
  body_template_json: "",
  enabled: true,
  is_fallback: false,
  priority: 0,
});

function buildExtra(form: FormState): Record<string, unknown> {
  let headers: Record<string, string> = {};
  try {
    headers = JSON.parse(form.headers_json || "{}");
  } catch {
    throw new Error("Custom headers must be valid JSON object");
  }
  const extra: Record<string, unknown> = {
    protocol: form.protocol,
    auth_scheme: form.auth_scheme,
  };
  if (form.auth_scheme === "custom" && form.auth_header.trim()) {
    extra.auth_header = form.auth_header.trim();
  }
  if (form.path.trim()) extra.path = form.path.trim();
  if (form.response_path.trim()) extra.response_path = form.response_path.trim();
  if (form.voice.trim()) extra.voice = form.voice.trim();
  if (headers && Object.keys(headers).length) extra.headers = headers;
  if (form.body_template_json.trim()) {
    try {
      extra.body_template = JSON.parse(form.body_template_json);
    } catch {
      throw new Error("Body template must be valid JSON");
    }
  }
  return extra;
}

export function ProvidersPage() {
  const [rows, setRows] = useState<ProviderConfig[]>([]);
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [editing, setEditing] = useState<ProviderConfig | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm());
  const [filterKind, setFilterKind] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  async function refresh() {
    const [list, cat] = await Promise.all([api.providers.list(), api.providers.catalog()]);
    setRows(list);
    setCatalog(cat);
    if (!templateId && cat.templates[0]) setTemplateId(cat.templates[0].id);
  }

  useEffect(() => {
    void refresh().catch((err) => setError(String(err)));
  }, []);

  const kinds = useMemo(() => {
    const fromRows = rows.map((r) => r.kind);
    const suggested = catalog?.suggested_kinds || [];
    return Array.from(new Set([...suggested, ...fromRows])).sort();
  }, [rows, catalog]);

  const filtered = filterKind ? rows.filter((r) => r.kind === filterKind) : rows;

  const protocolsForKind = useMemo(() => {
    const all = catalog?.protocols || [];
    return all.filter((p) => !form.kind || p.kinds.includes(form.kind) || p.kinds.length === 0);
  }, [catalog, form.kind]);

  function startEdit(row: ProviderConfig) {
    const extra = row.extra || {};
    setEditing(row);
    setForm({
      kind: row.kind,
      provider: row.provider,
      api_key: "",
      clear_api_key: false,
      base_url: row.base_url,
      model: row.model,
      protocol: String(extra.protocol || row.protocol || "openai_chat"),
      auth_scheme: String(extra.auth_scheme || "bearer"),
      auth_header: String(extra.auth_header || ""),
      path: String(extra.path || ""),
      response_path: String(extra.response_path || ""),
      voice: String(extra.voice || ""),
      headers_json: JSON.stringify(extra.headers || {}, null, 2),
      body_template_json: extra.body_template ? JSON.stringify(extra.body_template, null, 2) : "",
      enabled: row.enabled,
      is_fallback: row.is_fallback,
      priority: row.priority,
    });
    setShowAdvanced(true);
    setSaved("");
  }

  function applyProtocolDefaults(protocolId: string) {
    const tpl = catalog?.templates.find((t) => t.extra?.protocol === protocolId);
    setForm((prev) => {
      const next = { ...prev, protocol: protocolId };
      if (tpl) {
        next.path = String(tpl.extra?.path || prev.path);
        next.auth_scheme = String(tpl.extra?.auth_scheme || prev.auth_scheme);
        if (!prev.base_url) next.base_url = tpl.base_url || "";
        if (!prev.model) next.model = tpl.model || "";
      }
      return next;
    });
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaved("");
    try {
      const extra = buildExtra(form);
      const body = {
        kind: form.kind.trim(),
        provider: form.provider.trim(),
        api_key: form.api_key,
        clear_api_key: form.clear_api_key,
        base_url: form.base_url.trim(),
        model: form.model.trim(),
        extra,
        enabled: form.enabled,
        is_fallback: form.is_fallback,
        priority: form.priority,
      };
      if (editing) {
        await api.providers.update(editing.id, body);
      } else {
        await api.providers.create(body);
      }
      setEditing(null);
      setForm(emptyForm());
      setSaved("Provider saved.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function addFromTemplate() {
    if (!templateId) return;
    setError("");
    try {
      await api.providers.fromTemplate({
        template_id: templateId,
        api_key: form.api_key,
        enabled: true,
        is_fallback: form.is_fallback,
        priority: form.priority,
      });
      setSaved(`Added from template ${templateId}`);
      setForm((f) => ({ ...f, api_key: "" }));
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
          Free-form slots — any kind, name, base URL, and wire protocol. Optional templates are starters only;
          nothing is hardcoded at runtime.
        </p>
      </div>

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <div className="panel-title">Add from optional template</div>
        <div className="row-actions" style={{ alignItems: "end" }}>
          <div className="field" style={{ flex: 1, marginBottom: 0 }}>
            <label>Template</label>
            <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
              {(catalog?.templates || []).map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ flex: 1, marginBottom: 0 }}>
            <label>API secret (optional)</label>
            <input
              type="password"
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              placeholder="Paste key then add template"
              autoComplete="off"
            />
          </div>
          <button className="btn btn-primary" type="button" onClick={() => void addFromTemplate()}>
            Add template
          </button>
        </div>
      </div>

      <div className="grid-2">
        <div className="panel">
          <div className="panel-title" style={{ display: "flex", justifyContent: "space-between" }}>
            <span>Configured providers</span>
            <select
              value={filterKind}
              onChange={(e) => setFilterKind(e.target.value)}
              style={{
                background: "transparent",
                border: "1px solid var(--line)",
                borderRadius: 6,
                color: "var(--muted)",
                padding: "0.2rem 0.4rem",
              }}
            >
              <option value="">all kinds</option>
              {kinds.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Name</th>
                <th>Protocol</th>
                <th>Model</th>
                <th>Key</th>
                <th>Flags</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={r.id}>
                  <td>
                    <span className="badge info">{r.kind}</span>
                  </td>
                  <td>
                    {r.provider}
                    <div style={{ color: "var(--muted)", fontSize: "0.7rem" }}>{r.base_url || "—"}</div>
                  </td>
                  <td style={{ fontSize: "0.75rem" }}>{r.protocol || String(r.extra?.protocol || "—")}</td>
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
          {filtered.length === 0 && (
            <div className="empty">No providers yet — add a template or create a custom slot.</div>
          )}
        </div>

        <div className="panel">
          <div className="panel-title">{editing ? `Edit #${editing.id}` : "Custom provider"}</div>
          <form onSubmit={onSubmit}>
            <div className="field">
              <label>Kind (free text)</label>
              <input
                list="kind-suggestions"
                value={form.kind}
                onChange={(e) => setForm({ ...form, kind: e.target.value })}
                placeholder="llm / stt / tts / vad / mcp / custom"
                required
              />
              <datalist id="kind-suggestions">
                {kinds.map((k) => (
                  <option key={k} value={k} />
                ))}
              </datalist>
            </div>
            <div className="field">
              <label>Provider name</label>
              <input
                value={form.provider}
                onChange={(e) => setForm({ ...form, provider: e.target.value })}
                placeholder="any label — my-llm, acme-stt, local-ollama…"
                required
              />
            </div>
            <div className="field">
              <label>Protocol</label>
              <select
                value={form.protocol}
                onChange={(e) => applyProtocolDefaults(e.target.value)}
              >
                {(protocolsForKind.length ? protocolsForKind : catalog?.protocols || []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label} ({p.id})
                  </option>
                ))}
                {!protocolsForKind.find((p) => p.id === form.protocol) && form.protocol && (
                  <option value={form.protocol}>{form.protocol}</option>
                )}
              </select>
            </div>
            <div className="field">
              <label>
                API secret{" "}
                {editing ? `(leave blank to keep ${editing.api_key_masked || "existing"})` : ""}
              </label>
              <input
                type="password"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value, clear_api_key: false })}
                placeholder="optional depending on auth scheme"
                autoComplete="off"
              />
            </div>
            {editing && (
              <label className="switch" style={{ marginBottom: "0.75rem" }}>
                <input
                  type="checkbox"
                  checked={form.clear_api_key}
                  onChange={(e) => setForm({ ...form, clear_api_key: e.target.checked, api_key: "" })}
                />
                Clear stored secret
              </label>
            )}
            <div className="field">
              <label>Base URL</label>
              <input
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                placeholder="https://… or http://127.0.0.1:11434/v1"
              />
            </div>
            <div className="field">
              <label>Model</label>
              <input
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
                placeholder="model id for this provider"
              />
            </div>
            <div className="field">
              <label>Auth scheme</label>
              <select
                value={form.auth_scheme}
                onChange={(e) => setForm({ ...form, auth_scheme: e.target.value })}
              >
                {(catalog?.auth_schemes || ["bearer", "token", "x-api-key", "none", "custom"]).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            {form.auth_scheme === "custom" && (
              <div className="field">
                <label>Custom auth header</label>
                <input
                  value={form.auth_header}
                  onChange={(e) => setForm({ ...form, auth_header: e.target.value })}
                  placeholder="Authorization: Bearer {api_key}"
                />
              </div>
            )}
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

            <div style={{ marginTop: "0.85rem" }}>
              <button
                className="btn btn-ghost"
                type="button"
                onClick={() => setShowAdvanced((v) => !v)}
              >
                {showAdvanced ? "Hide" : "Show"} advanced wire options
              </button>
            </div>
            {showAdvanced && (
              <div style={{ marginTop: "0.75rem" }}>
                <div className="field">
                  <label>Endpoint path</label>
                  <input
                    value={form.path}
                    onChange={(e) => setForm({ ...form, path: e.target.value })}
                    placeholder="/chat/completions"
                  />
                </div>
                <div className="field">
                  <label>Response JSON path</label>
                  <input
                    value={form.response_path}
                    onChange={(e) => setForm({ ...form, response_path: e.target.value })}
                    placeholder="choices.0.message.content"
                  />
                </div>
                <div className="field">
                  <label>Voice (TTS)</label>
                  <input
                    value={form.voice}
                    onChange={(e) => setForm({ ...form, voice: e.target.value })}
                    placeholder="alloy"
                  />
                </div>
                <div className="field">
                  <label>Extra headers (JSON)</label>
                  <textarea
                    rows={3}
                    value={form.headers_json}
                    onChange={(e) => setForm({ ...form, headers_json: e.target.value })}
                  />
                </div>
                <div className="field">
                  <label>Body template (JSON, http_json / http_binary)</label>
                  <textarea
                    rows={4}
                    value={form.body_template_json}
                    onChange={(e) => setForm({ ...form, body_template_json: e.target.value })}
                    placeholder='{"prompt":"{{prompt}}","model":"{{model}}"}'
                  />
                </div>
              </div>
            )}

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
                    setForm(emptyForm());
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
