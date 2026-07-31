const API_BASE = import.meta.env.VITE_API_BASE || "/api";

export interface ProviderConfig {
  id: number;
  kind: string;
  provider: string;
  api_key: string;
  api_key_masked: string;
  base_url: string;
  model: string;
  extra: Record<string, unknown>;
  enabled: boolean;
  is_fallback: boolean;
  priority: number;
  updated_at: string;
  protocol?: string;
}

export interface ProviderCatalog {
  suggested_kinds: string[];
  protocols: Array<{ id: string; kinds: string[]; label: string }>;
  templates: Array<{
    id: string;
    label: string;
    kind: string;
    provider: string;
    base_url: string;
    model: string;
    extra: Record<string, unknown>;
  }>;
  auth_schemes: string[];
  note: string;
}

export interface CallRecord {
  id: number;
  channel: string;
  title: string;
  status: string;
  started_at: string;
  ended_at?: string | null;
  transcript_preview: string;
  message_count: number;
  fault_events: number;
  metadata: Record<string, unknown>;
}

export interface Message {
  id: number;
  call_id: number;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  created_at: string;
  meta: Record<string, unknown>;
}

export interface TransactionEvent {
  id: string;
  call_id?: number | null;
  kind: string;
  status: string;
  title: string;
  detail: string;
  provider: string;
  latency_ms?: number | null;
  created_at: string;
  meta: Record<string, unknown>;
}

export interface RagDocument {
  id: number;
  title: string;
  content: string;
  tags: string[];
  source: string;
  created_at: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<{ ok: boolean; pipeline: Record<string, unknown>; rag_docs: number }>("/health"),
  providers: {
    list: (kind?: string) =>
      request<ProviderConfig[]>(kind ? `/providers?kind=${encodeURIComponent(kind)}` : "/providers"),
    catalog: () => request<ProviderCatalog>("/providers/catalog"),
    create: (body: Record<string, unknown>) =>
      request<ProviderConfig>("/providers", { method: "POST", body: JSON.stringify(body) }),
    update: (id: number, body: Record<string, unknown>) =>
      request<ProviderConfig>(`/providers/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    fromTemplate: (body: Record<string, unknown>) =>
      request<ProviderConfig>("/providers/from-template", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    remove: (id: number) => request<{ ok: boolean }>(`/providers/${id}`, { method: "DELETE" }),
  },
  calls: {
    list: () => request<CallRecord[]>("/calls"),
    get: (id: number) => request<CallRecord>(`/calls/${id}`),
    create: (channel = "hybrid", title = "") =>
      request<CallRecord>("/calls", {
        method: "POST",
        body: JSON.stringify({ channel, title }),
      }),
    end: (id: number) => request<CallRecord>(`/calls/${id}/end`, { method: "POST" }),
    messages: (id: number) => request<Message[]>(`/calls/${id}/messages`),
  },
  chat: (content: string, call_id?: number | null, channel = "hybrid") =>
    request<{
      call_id: number;
      user_message: Message;
      assistant_message: Message;
      rag_context: string[];
      tools_used: string[];
      narrations?: Array<{ phase: string; text: string; speak?: boolean }>;
    }>("/chat", {
      method: "POST",
      body: JSON.stringify({ content, call_id, channel }),
    }),
  transactions: () => request<TransactionEvent[]>("/transactions"),
  rag: {
    list: () => request<RagDocument[]>("/rag/documents"),
    create: (body: { title: string; content: string; tags?: string[]; source?: string }) =>
      request<RagDocument>("/rag/documents", { method: "POST", body: JSON.stringify(body) }),
  },
  mcp: () =>
    request<{
      tools: Array<{ name: string; description: string }>;
      network_state: Record<string, unknown>;
    }>("/mcp/tools"),
};

export function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  return `${proto}://${host}${API_BASE}${path}`;
}
