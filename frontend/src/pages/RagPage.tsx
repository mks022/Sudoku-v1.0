import { FormEvent, useEffect, useState } from "react";
import { api, RagDocument } from "../lib/api";

export function RagPage() {
  const [docs, setDocs] = useState<RagDocument[]>([]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("manual,runbook");
  const [error, setError] = useState("");

  async function refresh() {
    setDocs(await api.rag.list());
  }

  useEffect(() => {
    void refresh().catch((err) => setError(String(err)));
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api.rag.create({
        title,
        content,
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        source: "manual",
      });
      setTitle("");
      setContent("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="page">
      <div style={{ marginBottom: "1rem" }}>
        <h2>RAG knowledge</h2>
        <p style={{ color: "var(--muted)", margin: "0.35rem 0 0" }}>
          Past outages and performance notes are loaded from files and this store, then injected into the LLM prompt.
        </p>
      </div>

      <div className="grid-2">
        <div className="panel">
          <div className="panel-title">Manual documents</div>
          <div className="scroll-y-tall">
            {docs.map((d) => (
              <div key={d.id} style={{ padding: "0.75rem 0", borderBottom: "1px solid var(--line)" }}>
                <div style={{ fontFamily: "var(--font-display)", fontWeight: 700 }}>{d.title}</div>
                <div style={{ color: "var(--muted)", fontSize: "0.75rem", margin: "0.25rem 0" }}>
                  {d.source} · {(d.tags || []).join(", ")}
                </div>
                <div style={{ whiteSpace: "pre-wrap", fontSize: "0.85rem" }}>{d.content.slice(0, 400)}</div>
              </div>
            ))}
            {docs.length === 0 && <div className="empty">No manual docs — seed files still load from disk.</div>}
          </div>
        </div>

        <div className="panel">
          <div className="panel-title">Add operator note</div>
          <form onSubmit={onSubmit}>
            <div className="field">
              <label>Title</label>
              <input value={title} onChange={(e) => setTitle(e.target.value)} required />
            </div>
            <div className="field">
              <label>Content</label>
              <textarea rows={10} value={content} onChange={(e) => setContent(e.target.value)} required />
            </div>
            <div className="field">
              <label>Tags (comma-separated)</label>
              <input value={tags} onChange={(e) => setTags(e.target.value)} />
            </div>
            <button className="btn btn-primary" type="submit">
              Index document
            </button>
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
