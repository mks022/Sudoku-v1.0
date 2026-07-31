import { FormEvent, useEffect, useRef, useState } from "react";
import { api, Message, TransactionEvent, wsUrl } from "../lib/api";

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

function speakText(text: string) {
  if (!text || !("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text.slice(0, 500));
  u.rate = 1.05;
  window.speechSynthesis.speak(u);
}

export function ConsoleChat({
  liveEvents = [],
}: {
  liveEvents?: TransactionEvent[];
}) {
  const [callId, setCallId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [speakLive, setSpeakLive] = useState(true);
  const [ragHints, setRagHints] = useState<string[]>([]);
  const [tools, setTools] = useState<string[]>([]);
  const [phase, setPhase] = useState("");
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const recognitionRef = useRef<SpeechRec | null>(null);
  const voiceWsRef = useRef<WebSocket | null>(null);
  const seenNarrationKeys = useRef<Set<string>>(new Set());
  const callIdRef = useRef<number | null>(null);

  useEffect(() => {
    callIdRef.current = callId;
  }, [callId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    return () => {
      recognitionRef.current?.stop();
      voiceWsRef.current?.close();
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    };
  }, []);

  // Engage live troubleshooting breadcrumbs into the conversation + TTS
  useEffect(() => {
    if (!liveEvents.length) return;
    const latest = liveEvents[liveEvents.length - 1];
    if (!latest || latest.kind !== "narration") return;
    if (callIdRef.current && latest.call_id && latest.call_id !== callIdRef.current) return;

    const text = latest.detail || latest.title;
    const phaseName = String(latest.meta?.phase || "step");
    const key = `${latest.call_id || callIdRef.current}:${phaseName}:${text}`;
    if (seenNarrationKeys.current.has(key)) return;
    seenNarrationKeys.current.add(key);
    // also mark event id
    if (latest.id) seenNarrationKeys.current.add(latest.id);    setPhase(phaseName);
    setBusy(true);

    const msg: Message = {
      id: Number(latest.meta?.message_id) || Date.now(),
      call_id: latest.call_id || callIdRef.current || 0,
      role: "assistant",
      content: text,
      created_at: latest.created_at,
      meta: { narration: true, phase: phaseName, speak: true },
    };
    setMessages((prev) => {
      if (prev.some((m) => m.id === msg.id && m.content === msg.content)) return prev;
      return [...prev, msg];
    });

    if (speakLive && latest.meta?.speak !== false) {
      speakText(text);
    }

    if (phaseName === "wrap") {
      setBusy(false);
      setPhase("");
    }
  }, [liveEvents, speakLive]);

  async function ensureSession(): Promise<number> {
    if (callId) return callId;
    const call = await api.calls.create("hybrid", "Live console session");
    setCallId(call.id);
    callIdRef.current = call.id;
    return call.id;
  }

  async function openVoiceSocket(id: number) {
    if (voiceWsRef.current && voiceWsRef.current.readyState <= 1) return;
    const ws = new WebSocket(wsUrl(`/ws/voice/${id}`));
    voiceWsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "narration" && msg.text) {
          const key = `${id}:${msg.phase || "step"}:${msg.text}`;
          if (seenNarrationKeys.current.has(key)) return;
          seenNarrationKeys.current.add(key);
          if (msg.message_id) seenNarrationKeys.current.add(String(msg.message_id));
          setPhase(String(msg.phase || "step"));
          setBusy(true);
          setMessages((prev) => [
            ...prev,
            {
              id: msg.message_id || Date.now(),
              call_id: id,
              role: "assistant",
              content: msg.text,
              created_at: new Date().toISOString(),
              meta: { narration: true, phase: msg.phase, speak: true },
            },
          ]);
          if (speakLive && msg.speak !== false) speakText(msg.text);
        } else if (msg.type === "narration_audio" && msg.audio_b64) {          try {
            const bytes = Uint8Array.from(atob(msg.audio_b64), (c) => c.charCodeAt(0));
            const blob = new Blob([bytes], { type: msg.mime || "audio/mpeg" });
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            void audio.play();
          } catch {
            /* browser TTS already covers narration text */
          }
        } else if (msg.type === "pass_aborted" || msg.type === "abort_ack") {
          if ("speechSynthesis" in window) window.speechSynthesis.cancel();
          setPhase("aborted");
          setMessages((prev) => [
            ...prev,
            {
              id: Date.now(),
              call_id: id,
              role: "assistant",
              content:
                msg.text ||
                "Prior MCP approach aborted — tool threads closed. Starting the new direction.",
              created_at: new Date().toISOString(),
              meta: { narration: true, phase: "abort", abort_info: msg.abort_info },
            },
          ]);
          if (speakLive) {
            speakText("Aborting the current approach and closing MCP threads.");
          }
        } else if (msg.type === "assistant_text") {
          setMessages((prev) => [
            ...prev,
            {
              id: Date.now(),
              call_id: id,
              role: "assistant",
              content: msg.text,
              created_at: new Date().toISOString(),
              meta: { final: true },
            },
          ]);
          setTools(msg.tools || []);
          setRagHints(msg.rag || []);
          setBusy(false);
          setPhase("");
          if (msg.speak && speakLive) speakText(msg.text);
        }
      } catch {
        /* ignore */
      }
    };
  }

  async function sendText(text: string) {
    const trimmed = text.trim();
    if (!trimmed) return;
    // Allow follow-ups while troubleshooting; only block exact empty double-sends
    setError("");
    try {
      const id = await ensureSession();
      await openVoiceSocket(id);
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
      setBusy(true);
      setPhase("start");

      // Prefer voice/hybrid WS so narrations stream; abort prior MCP pass if busy
      const ws = voiceWsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        if ("speechSynthesis" in window) window.speechSynthesis.cancel();
        ws.send(
          JSON.stringify({
            type: busy ? "abort_and_chat" : "chat",
            text: trimmed,
            reason: busy ? "operator_new_approach" : "operator_message",
          })
        );
        return;
      }

      if (busy && "speechSynthesis" in window) window.speechSynthesis.cancel();
      const res = await api.chat(trimmed, id, "hybrid", {
        abort_current: true,
        supersede_reason: busy ? "operator_new_approach" : "operator_message",
      });
      setCallId(res.call_id);
      setMessages((prev) => {
        const withoutOpt = prev.filter((m) => m.id !== optimistic.id);
        // Narrations may already be present from the live transaction stream.
        const hasFinal = withoutOpt.some(
          (m) => m.role === "assistant" && !m.meta?.narration && m.content === res.assistant_message.content
        );
        const next = [...withoutOpt, res.user_message];
        if (!hasFinal) next.push(res.assistant_message);
        return next;
      });
      setRagHints(res.rag_context || []);
      setTools(res.tools_used || []);
      setBusy(false);
      setPhase("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
      setPhase("");
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
        setBusy(true);
        setPhase("start");
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
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    setCallId(null);
    setMessages([]);
    setTools([]);
    setRagHints([]);
    setBusy(false);
    setPhase("");
    seenNarrationKeys.current.clear();
  }

  return (
    <div className="panel" style={{ display: "flex", flexDirection: "column", minHeight: 520 }}>
      <div className="panel-title" style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem", flexWrap: "wrap" }}>
        <span>Operator channel · live troubleshooting</span>
        <span style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
          {busy && (
            <span className="badge warn">
              <span className="live-dot" style={{ background: "var(--warn)" }} />
              troubleshooting{phase ? ` · ${phase}` : ""}
            </span>
          )}
          <span className="badge info">call {callId ?? "—"}</span>
        </span>
      </div>

      <div className="scroll-y" style={{ flex: 1 }}>
        {messages.length === 0 && (
          <div className="empty">
            Ask about BGP flaps, DNS timeouts, PoP health, or say “simulate fault at AMS-1”.
            While I mitigate, I’ll narrate each breadcrumb — you can keep talking.
          </div>
        )}
        {messages.map((m) => (
          <div
            key={`${m.id}-${m.created_at}-${(m.meta?.phase as string) || ""}`}
            className={`msg ${m.role}${m.meta?.narration ? " narration" : ""}`}
          >
            {Boolean(m.meta?.narration) && (
              <div style={{ fontSize: "0.68rem", color: "var(--muted)", marginBottom: 4 }}>
                live · {String(m.meta?.phase || "step")}
              </div>
            )}
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

      <label className="switch" style={{ marginTop: "0.75rem" }}>
        <input type="checkbox" checked={speakLive} onChange={(e) => setSpeakLive(e.target.checked)} />
        Speak troubleshooting breadcrumbs (TTS)
      </label>

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
          placeholder={
            busy
              ? "Suggest a different approach — current MCP pass will abort cleanly…"
              : "Type a mitigation request…"
          }
        />
        <button className="btn btn-primary" type="submit" disabled={!input.trim()}>
          {busy ? "Abort & apply" : "Send"}
        </button>
        {busy && callId && (
          <button
            className="btn btn-ghost"
            type="button"
            onClick={() => {
              if ("speechSynthesis" in window) window.speechSynthesis.cancel();
              void api.abortPass(callId, "operator_abort").then(() => {
                setBusy(false);
                setPhase("");
                setMessages((prev) => [
                  ...prev,
                  {
                    id: Date.now(),
                    call_id: callId,
                    role: "assistant",
                    content: "MCP pass aborted on request. Tool threads closed.",
                    created_at: new Date().toISOString(),
                    meta: { narration: true, phase: "abort" },
                  },
                ]);
              });
              voiceWsRef.current?.send(JSON.stringify({ type: "abort", reason: "operator_abort" }));
            }}
          >
            Abort
          </button>
        )}
        <button className="btn btn-ghost" type="button" onClick={() => void endSession()} disabled={!callId}>
          End
        </button>
      </form>
    </div>
  );
}
