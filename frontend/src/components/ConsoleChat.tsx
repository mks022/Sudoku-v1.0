import { FormEvent, useEffect, useRef, useState } from "react";
import { api, Message, wsUrl } from "../lib/api";

type SpeechRec = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((ev: { results: ArrayLike<{ 0: { transcript: string }; isFinal: boolean }> }) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};

function getSpeechRecognition(): (new () => SpeechRec) | null {
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRec;
    webkitSpeechRecognition?: new () => SpeechRec;
  };
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function ConsoleChat() {
  const [callId, setCallId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [ragHints, setRagHints] = useState<string[]>([]);
  const [tools, setTools] = useState<string[]>([]);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const recognitionRef = useRef<SpeechRec | null>(null);
  const voiceWsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    return () => {
      recognitionRef.current?.stop();
      voiceWsRef.current?.close();
    };
  }, []);

  async function ensureSession(): Promise<number> {
    if (callId) return callId;
    const call = await api.calls.create("hybrid", "Live console session");
    setCallId(call.id);
    return call.id;
  }

  async function openVoiceSocket(id: number) {
    if (voiceWsRef.current && voiceWsRef.current.readyState <= 1) return;
    const ws = new WebSocket(wsUrl(`/ws/voice/${id}`));
    voiceWsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "assistant_text") {
          setMessages((prev) => [
            ...prev,
            {
              id: Date.now(),
              call_id: id,
              role: "assistant",
              content: msg.text,
              created_at: new Date().toISOString(),
              meta: {},
            },
          ]);
          setTools(msg.tools || []);
          setRagHints(msg.rag || []);
          if (msg.text && "speechSynthesis" in window) {
            const u = new SpeechSynthesisUtterance(msg.text.slice(0, 500));
            window.speechSynthesis.speak(u);
          }
        }
      } catch {
        /* ignore */
      }
    };
  }

  async function sendText(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError("");
    try {
      const id = await ensureSession();
      const optimistic: Message = {
        id: Date.now(),
        call_id: id,
        role: "user",
        content: trimmed,
        created_at: new Date().toISOString(),
        meta: {},
      };
      setMessages((prev) => [...prev, optimistic]);
      setInput("");
      const res = await api.chat(trimmed, id, "hybrid");
      setCallId(res.call_id);
      setMessages((prev) => {
        const withoutOpt = prev.filter((m) => m.id !== optimistic.id);
        return [...withoutOpt, res.user_message, res.assistant_message];
      });
      setRagHints(res.rag_context || []);
      setTools(res.tools_used || []);
      if (res.assistant_message.content && "speechSynthesis" in window && listening) {
        const u = new SpeechSynthesisUtterance(res.assistant_message.content.slice(0, 500));
        window.speechSynthesis.speak(u);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void sendText(input);
  }

  async function toggleMic() {
    const SR = getSpeechRecognition();
    if (!SR) {
      setError("Browser speech recognition unavailable — type instead, or configure STT provider keys.");
      return;
    }
    if (listening) {
      recognitionRef.current?.stop();
      setListening(false);
      return;
    }
    const id = await ensureSession();
    await openVoiceSocket(id);
    const rec = new SR();
    rec.continuous = false;
    rec.interimResults = false;
    rec.lang = "en-US";
    rec.onresult = (ev) => {
      const transcript = ev.results[0]?.[0]?.transcript || "";
      if (!transcript) return;
      const ws = voiceWsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "utterance_end", transcript }));
        setMessages((prev) => [
          ...prev,
          {
            id: Date.now(),
            call_id: id,
            role: "user",
            content: transcript,
            created_at: new Date().toISOString(),
            meta: { via: "voice" },
          },
        ]);
      } else {
        void sendText(transcript);
      }
    };
    rec.onerror = () => setListening(false);
    rec.onend = () => setListening(false);
    recognitionRef.current = rec;
    rec.start();
    setListening(true);
  }

  async function endSession() {
    if (!callId) return;
    await api.calls.end(callId);
    voiceWsRef.current?.send(JSON.stringify({ type: "close" }));
    voiceWsRef.current?.close();
    setCallId(null);
    setMessages([]);
    setTools([]);
    setRagHints([]);
  }

  return (
    <div className="panel" style={{ display: "flex", flexDirection: "column", minHeight: 520 }}>
      <div className="panel-title" style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem" }}>
        <span>Operator channel · voice + chat</span>
        <span className="badge info">call {callId ?? "—"}</span>
      </div>

      <div className="scroll-y" style={{ flex: 1 }}>
        {messages.length === 0 && (
          <div className="empty">
            Ask about BGP flaps, DNS timeouts, PoP health, or say “simulate fault at AMS-1”.
            Keys go in Providers — demo mode works without them.
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            {m.content}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {(ragHints.length > 0 || tools.length > 0) && (
        <div style={{ marginTop: "0.75rem", fontSize: "0.75rem", color: "var(--muted)" }}>
          {ragHints.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <strong style={{ color: "var(--ink)" }}>RAG</strong>
              <ul style={{ margin: "0.25rem 0 0", paddingLeft: "1.1rem" }}>
                {ragHints.slice(0, 3).map((h) => (
                  <li key={h}>{h.slice(0, 160)}</li>
                ))}
              </ul>
            </div>
          )}
          {tools.length > 0 && (
            <div>
              <strong style={{ color: "var(--ink)" }}>MCP</strong>
              <ul style={{ margin: "0.25rem 0 0", paddingLeft: "1.1rem" }}>
                {tools.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="badge danger" style={{ marginTop: 8 }}>
          {error}
        </div>
      )}

      <form className="chat-compose" onSubmit={onSubmit}>
        <button
          type="button"
          className="btn mic-btn"
          data-active={listening ? "true" : "false"}
          onClick={() => void toggleMic()}
          title="Push to talk"
        >
          {listening ? "Listening…" : "Mic"}
        </button>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a mitigation request…"
          disabled={busy}
        />
        <button className="btn btn-primary" type="submit" disabled={busy || !input.trim()}>
          Send
        </button>
        <button className="btn btn-ghost" type="button" onClick={() => void endSession()} disabled={!callId}>
          End
        </button>
      </form>
    </div>
  );
}
