"""Protocol-driven provider adapters — no hardcoded vendor lock-in.

Each ProviderConfig row is interpreted via:
  - kind: free string (llm | stt | tts | vad | mcp | custom…)
  - provider: display / circuit-breaker label (any name)
  - base_url, model, api_key
  - extra.protocol: which wire protocol to use
  - extra.auth_scheme / auth_header / headers / path / response_path / …

Presets in TEMPLATES are optional UI helpers only — nothing is seeded into the DB.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.config import settings

# Suggested kinds for the UI (not enforced by the API).
SUGGESTED_KINDS = ["llm", "stt", "tts", "vad", "mcp"]

# Optional starter templates — applied only when the user clicks "Add from template".
TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "openai-chat",
        "label": "OpenAI-compatible chat (LLM)",
        "kind": "llm",
        "provider": "openai-compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "extra": {
            "protocol": "openai_chat",
            "auth_scheme": "bearer",
            "path": "/chat/completions",
            "temperature": 0.3,
        },
    },
    {
        "id": "anthropic-messages",
        "label": "Anthropic Messages (LLM)",
        "kind": "llm",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "model": "claude-3-5-haiku-latest",
        "extra": {
            "protocol": "anthropic_messages",
            "auth_scheme": "x-api-key",
            "path": "/v1/messages",
            "anthropic_version": "2023-06-01",
            "max_tokens": 1024,
        },
    },
    {
        "id": "ollama-chat",
        "label": "Ollama local chat (LLM)",
        "kind": "llm",
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "llama3.2",
        "extra": {
            "protocol": "openai_chat",
            "auth_scheme": "none",
            "path": "/chat/completions",
        },
    },
    {
        "id": "http-json-llm",
        "label": "Generic HTTP JSON (LLM)",
        "kind": "llm",
        "provider": "custom-http",
        "base_url": "https://example.com/v1",
        "model": "",
        "extra": {
            "protocol": "http_json",
            "auth_scheme": "bearer",
            "path": "/generate",
            "method": "POST",
            "body_template": {
                "model": "{{model}}",
                "prompt": "{{prompt}}",
            },
            "response_path": "text",
        },
    },
    {
        "id": "openai-whisper",
        "label": "OpenAI-compatible Whisper (STT)",
        "kind": "stt",
        "provider": "whisper-compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "whisper-1",
        "extra": {
            "protocol": "openai_transcriptions",
            "auth_scheme": "bearer",
            "path": "/audio/transcriptions",
        },
    },
    {
        "id": "deepgram-listen",
        "label": "Deepgram listen (STT)",
        "kind": "stt",
        "provider": "deepgram",
        "base_url": "https://api.deepgram.com",
        "model": "nova-2",
        "extra": {
            "protocol": "deepgram_listen",
            "auth_scheme": "token",
            "path": "/v1/listen",
        },
    },
    {
        "id": "openai-speech",
        "label": "OpenAI-compatible speech (TTS)",
        "kind": "tts",
        "provider": "speech-compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini-tts",
        "extra": {
            "protocol": "openai_speech",
            "auth_scheme": "bearer",
            "path": "/audio/speech",
            "voice": "alloy",
        },
    },
    {
        "id": "http-binary-tts",
        "label": "Generic HTTP binary audio (TTS)",
        "kind": "tts",
        "provider": "custom-tts",
        "base_url": "https://example.com",
        "model": "",
        "extra": {
            "protocol": "http_binary",
            "auth_scheme": "bearer",
            "path": "/tts",
            "method": "POST",
            "body_template": {"text": "{{text}}", "model": "{{model}}"},
            "content_type": "application/json",
        },
    },
    {
        "id": "silero-vad",
        "label": "Silero VAD (local)",
        "kind": "vad",
        "provider": "silero",
        "base_url": "",
        "model": "silero_vad",
        "extra": {"protocol": "local", "auth_scheme": "none"},
    },
    {
        "id": "builtin-mcp",
        "label": "Builtin network MCP tools",
        "kind": "mcp",
        "provider": "builtin-network",
        "base_url": "",
        "model": "",
        "extra": {"protocol": "builtin", "transport": "in-process", "auth_scheme": "none"},
    },
    {
        "id": "demo-llm",
        "label": "Demo LLM (no API key)",
        "kind": "llm",
        "provider": "demo",
        "base_url": "",
        "model": "rule-based",
        "extra": {"protocol": "demo", "auth_scheme": "none"},
    },
]

PROTOCOLS = [
    {"id": "openai_chat", "kinds": ["llm"], "label": "OpenAI chat completions"},
    {"id": "anthropic_messages", "kinds": ["llm"], "label": "Anthropic messages"},
    {"id": "http_json", "kinds": ["llm", "stt", "tts", "mcp"], "label": "Generic HTTP JSON"},
    {"id": "demo", "kinds": ["llm"], "label": "Built-in demo brain"},
    {"id": "openai_transcriptions", "kinds": ["stt"], "label": "OpenAI audio transcriptions"},
    {"id": "deepgram_listen", "kinds": ["stt"], "label": "Deepgram listen"},
    {"id": "openai_speech", "kinds": ["tts"], "label": "OpenAI audio speech"},
    {"id": "http_binary", "kinds": ["tts", "stt"], "label": "Generic HTTP binary"},
    {"id": "local", "kinds": ["vad"], "label": "Local / in-process"},
    {"id": "builtin", "kinds": ["mcp"], "label": "Builtin MCP"},
]


def _extra(cfg) -> dict[str, Any]:
    return dict(cfg.extra or {})


def resolve_protocol(cfg) -> str:
    extra = _extra(cfg)
    if extra.get("protocol"):
        return str(extra["protocol"])
    # Soft inference from provider name only when protocol omitted (legacy rows).
    name = (cfg.provider or "").lower()
    kind = (cfg.kind or "").lower()
    if kind == "llm":
        if "anthropic" in name or "claude" in name:
            return "anthropic_messages"
        if name in {"demo", "builtin-demo"}:
            return "demo"
        return "openai_chat"
    if kind == "stt":
        if "deepgram" in name:
            return "deepgram_listen"
        return "openai_transcriptions"
    if kind == "tts":
        return "openai_speech"
    if kind == "vad":
        return "local"
    if kind == "mcp":
        return "builtin"
    return "http_json"


def build_auth_headers(cfg) -> dict[str, str]:
    extra = _extra(cfg)
    scheme = str(extra.get("auth_scheme") or "bearer").lower()
    key = cfg.api_key or ""
    headers: dict[str, str] = {}

    custom = extra.get("auth_header")
    if custom and key:
        # e.g. "Authorization: Bearer {api_key}" or "X-API-Key: {api_key}"
        line = str(custom).replace("{api_key}", key)
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
        else:
            headers["Authorization"] = line
    elif scheme == "bearer" and key:
        headers["Authorization"] = f"Bearer {key}"
    elif scheme == "token" and key:
        headers["Authorization"] = f"Token {key}"
    elif scheme in {"x-api-key", "api_key"} and key:
        headers["x-api-key"] = key
    elif scheme == "none":
        pass

    # Merge arbitrary extra headers (values may include {api_key})
    for hk, hv in (extra.get("headers") or {}).items():
        headers[str(hk)] = str(hv).replace("{api_key}", key)
    return headers


def _dig(data: Any, path: str) -> Any:
    """Simple dotted / indexed path: choices.0.message.content"""
    cur = data
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _render_template(value: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(value, str):
        out = value
        for k, v in ctx.items():
            out = out.replace(f"{{{{{k}}}}}", str(v))
        return out
    if isinstance(value, list):
        return [_render_template(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: _render_template(v, ctx) for k, v in value.items()}
    return value


async def call_llm(cfg, *, system: str, prompt: str, user_text: str, demo_fn) -> str:
    protocol = resolve_protocol(cfg)
    extra = _extra(cfg)
    auth = str(extra.get("auth_scheme") or "bearer").lower()

    if protocol == "demo":
        return await demo_fn(user_text)
    # No credential and auth required → demo degrade (works offline / unconfigured).
    if not cfg.api_key and auth not in {"none"}:
        return await demo_fn(user_text)

    base = (cfg.base_url or "").rstrip("/")
    path = extra.get("path") or "/chat/completions"
    headers = build_auth_headers(cfg)
    headers.setdefault("Content-Type", "application/json")
    timeout = float(extra.get("timeout") or settings.request_timeout_seconds)

    if protocol in {"openai_chat", "openai_compat"}:
        if not base:
            raise RuntimeError(f"{cfg.provider}: base_url required for {protocol}")
        body = {
            "model": cfg.model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": float(extra.get("temperature", 0.3)),
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base}{path}", headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        path_expr = extra.get("response_path") or "choices.0.message.content"
        text = _dig(data, path_expr)
        if not text:
            raise RuntimeError(f"{cfg.provider}: empty LLM response at {path_expr}")
        return str(text)

    if protocol == "anthropic_messages":
        if not base:
            raise RuntimeError(f"{cfg.provider}: base_url required")
        headers.setdefault("anthropic-version", str(extra.get("anthropic_version") or "2023-06-01"))
        body = {
            "model": cfg.model or "claude-3-5-haiku-latest",
            "max_tokens": int(extra.get("max_tokens") or 1024),
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base}{path}", headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        path_expr = extra.get("response_path") or "content.0.text"
        text = _dig(data, path_expr)
        if not text:
            raise RuntimeError(f"{cfg.provider}: empty Anthropic response")
        return str(text)

    if protocol == "http_json":
        if not base:
            raise RuntimeError(f"{cfg.provider}: base_url required")
        method = str(extra.get("method") or "POST").upper()
        ctx = {"model": cfg.model or "", "prompt": prompt, "system": system, "user_text": user_text}
        body = _render_template(extra.get("body_template") or {"prompt": "{{prompt}}"}, ctx)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, f"{base}{path}", headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        path_expr = str(extra.get("response_path") or "text")
        text = _dig(data, path_expr) if not isinstance(data, str) else data
        if text is None:
            raise RuntimeError(f"{cfg.provider}: response_path `{path_expr}` missing")
        return str(text)

    raise RuntimeError(f"{cfg.provider}: unsupported LLM protocol `{protocol}`")


async def call_stt(cfg, audio: bytes) -> str:
    protocol = resolve_protocol(cfg)
    extra = _extra(cfg)
    base = (cfg.base_url or "").rstrip("/")
    headers = build_auth_headers(cfg)
    timeout = float(extra.get("timeout") or 60)
    path = extra.get("path")

    if protocol == "openai_transcriptions":
        if not base:
            raise RuntimeError(f"{cfg.provider}: base_url required")
        path = path or "/audio/transcriptions"
        files = {"file": ("audio.wav", audio, "audio/wav")}
        data = {"model": cfg.model or "whisper-1"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base}{path}", headers=headers, data=data, files=files)
            resp.raise_for_status()
            payload = resp.json()
        return str(_dig(payload, extra.get("response_path") or "text") or "")

    if protocol == "deepgram_listen":
        if not base:
            raise RuntimeError(f"{cfg.provider}: base_url required")
        path = path or "/v1/listen"
        model = cfg.model or "nova-2"
        headers.setdefault("Content-Type", extra.get("content_type") or "audio/wav")
        url = f"{base}{path}?model={model}&smart_format=true"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, content=audio)
            resp.raise_for_status()
            payload = resp.json()
        expr = extra.get("response_path") or "results.channels.0.alternatives.0.transcript"
        return str(_dig(payload, expr) or "")

    if protocol == "http_binary":
        if not base:
            raise RuntimeError(f"{cfg.provider}: base_url required")
        path = path or "/"
        method = str(extra.get("method") or "POST").upper()
        headers.setdefault("Content-Type", extra.get("content_type") or "audio/wav")
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, f"{base}{path}", headers=headers, content=audio)
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if "json" in ctype:
                payload = resp.json()
                return str(_dig(payload, extra.get("response_path") or "text") or "")
            return resp.text

    raise RuntimeError(f"{cfg.provider}: unsupported STT protocol `{protocol}`")


async def call_tts(cfg, text: str) -> str:
    """Return base64-encoded audio bytes."""
    protocol = resolve_protocol(cfg)
    extra = _extra(cfg)
    base = (cfg.base_url or "").rstrip("/")
    headers = build_auth_headers(cfg)
    timeout = float(extra.get("timeout") or 60)
    path = extra.get("path")

    if protocol == "openai_speech":
        if not base:
            raise RuntimeError(f"{cfg.provider}: base_url required")
        path = path or "/audio/speech"
        headers.setdefault("Content-Type", "application/json")
        body = {
            "model": cfg.model or "gpt-4o-mini-tts",
            "voice": extra.get("voice") or "alloy",
            "input": text[:4000],
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base}{path}", headers=headers, json=body)
            resp.raise_for_status()
            return base64.b64encode(resp.content).decode("ascii")

    if protocol in {"http_binary", "http_json"}:
        if not base:
            raise RuntimeError(f"{cfg.provider}: base_url required")
        path = path or "/"
        method = str(extra.get("method") or "POST").upper()
        headers.setdefault("Content-Type", extra.get("content_type") or "application/json")
        ctx = {"text": text[:4000], "model": cfg.model or "", "voice": extra.get("voice") or ""}
        body = _render_template(
            extra.get("body_template") or {"text": "{{text}}", "model": "{{model}}"},
            ctx,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, f"{base}{path}", headers=headers, json=body)
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if "json" in ctype:
                payload = resp.json()
                b64 = _dig(payload, extra.get("response_path") or "audio_b64")
                if b64:
                    return str(b64)
                raise RuntimeError(f"{cfg.provider}: no audio in JSON response")
            return base64.b64encode(resp.content).decode("ascii")

    raise RuntimeError(f"{cfg.provider}: unsupported TTS protocol `{protocol}`")


def catalog() -> dict[str, Any]:
    return {
        "suggested_kinds": SUGGESTED_KINDS,
        "protocols": PROTOCOLS,
        "templates": TEMPLATES,
        "auth_schemes": ["bearer", "token", "x-api-key", "none", "custom"],
        "note": (
            "Providers are free-form. Use templates as optional starters, "
            "or set kind/provider/protocol/base_url yourself. Nothing is hardcoded at runtime."
        ),
    }
