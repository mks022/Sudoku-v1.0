from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.mcp_tools.network import invoke_tool, tools_prompt_block
from app.models.db import CallRecord, Message, TransactionLog
from app.models.schemas import MessageRole, TransactionKind
from app.rag.index import rag_index
from app.resilience.executor import ProviderSlot, ResilientExecutor
from app.services.events import event_bus
from app.services.providers import providers_by_kind

SYSTEM_PROMPT = """You are NetGuard, a network fault mitigation voice/chat agent.
You help operators diagnose and mitigate network incidents using live status,
historical outages, and MCP mitigation tools.

Guidelines:
- Prefer actionable mitigation grounded in RAG history and current status.
- Be concise for voice: 1–3 short sentences unless the operator asks for detail.
- When using tools, emit ACTION JSON lines as instructed.
- If providers are degraded, recommend circuit breakers / failover.
- Never invent telemetry; call get_network_status when unsure.
"""

ACTION_LINE_RE = re.compile(r"^ACTION:\s*(\{.*\})\s*$", re.MULTILINE)


class AgentService:
    def __init__(self) -> None:
        self.executor = ResilientExecutor(
            failure_threshold=settings.circuit_failure_threshold,
            reset_seconds=settings.circuit_reset_seconds,
        )

    async def create_call(
        self, session: AsyncSession, channel: str = "hybrid", title: str = ""
    ) -> CallRecord:
        call = CallRecord(channel=channel, title=title or f"{channel.title()} session", status="active")
        session.add(call)
        await session.commit()
        await session.refresh(call)
        await event_bus.emit(
            TransactionKind.system,
            "Call started",
            status="started",
            detail=f"channel={channel}",
            call_id=call.id,
        )
        return call

    async def end_call(self, session: AsyncSession, call_id: int, status: str = "completed") -> CallRecord:
        call = await session.get(CallRecord, call_id)
        if not call:
            raise ValueError("Call not found")
        from datetime import datetime

        call.status = status
        call.ended_at = datetime.utcnow()
        await session.commit()
        await session.refresh(call)
        await event_bus.emit(
            TransactionKind.system,
            "Call ended",
            status=status,
            call_id=call.id,
        )
        return call

    async def list_calls(self, session: AsyncSession, limit: int = 50) -> list[CallRecord]:
        stmt = (
            select(CallRecord)
            .options(selectinload(CallRecord.messages))
            .order_by(CallRecord.started_at.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())

    async def get_call(self, session: AsyncSession, call_id: int) -> CallRecord | None:
        stmt = (
            select(CallRecord)
            .where(CallRecord.id == call_id)
            .options(selectinload(CallRecord.messages), selectinload(CallRecord.transactions))
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _persist_tx(
        self,
        session: AsyncSession,
        *,
        event_id: str,
        call_id: int | None,
        kind: str,
        status: str,
        title: str,
        detail: str = "",
        provider: str = "",
        latency_ms: float | None = None,
        meta: dict | None = None,
    ) -> None:
        session.add(
            TransactionLog(
                event_id=event_id,
                call_id=call_id,
                kind=kind,
                status=status,
                title=title,
                detail=detail,
                provider=provider,
                latency_ms=latency_ms,
                meta=meta or {},
            )
        )
        if call_id and status in {"retry", "fallback", "failed"}:
            call = await session.get(CallRecord, call_id)
            if call:
                call.fault_events = (call.fault_events or 0) + 1
        await session.commit()

    def _build_prompt(self, user_text: str, history: list[Message]) -> tuple[str, list[str]]:
        chunks = rag_index.query(user_text)
        rag_block = rag_index.format_prompt_block(chunks)
        tools_block = tools_prompt_block()
        hist = "\n".join(f"{m.role}: {m.content}" for m in history[-12:])
        prompt = (
            f"{SYSTEM_PROMPT}\n\n{rag_block}\n\n{tools_block}\n\n"
            f"Conversation:\n{hist}\nuser: {user_text}\nassistant:"
        )
        return prompt, [f"{c.title}: {c.content[:240]}" for c in chunks]

    async def _llm_openai(self, cfg, prompt: str, user_text: str) -> str:
        if not cfg.api_key:
            return await self._demo_llm(user_text)

        headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
        base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        body = {
            "model": cfg.model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
        }
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            resp = await client.post(f"{base}/chat/completions", headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def _demo_llm(self, user_text: str) -> str:
        """Offline-capable demo brain when no LLM API key is configured."""
        lower = user_text.lower()
        actions: list[dict[str, Any]] = []
        reply_parts: list[str] = []

        if any(k in lower for k in ("status", "health", "latency", "loss")):
            actions.append({"tool": "get_network_status", "args": {}})
            reply_parts.append("Checking live PoP health now.")
        if any(k in lower for k in ("flap", "bgp", "dampen")):
            peer = "AMS-1"
            if "ams-2" in lower:
                peer = "AMS-2"
            elif "dfw" in lower:
                peer = "DFW-E"
            alt = "AMS-2" if peer != "AMS-2" else "AMS-1"
            actions.append({"tool": "dampen_bgp_peer", "args": {"peer": peer, "minutes": 15}})
            actions.append(
                {"tool": "shift_anycast_weight", "args": {"from_pop": peer, "to_pop": alt, "amount": 30}}
            )
            reply_parts.append(
                f"This matches prior BGP flap playbooks. I will dampen {peer} and shift anycast weight to {alt}."
            )
        if any(k in lower for k in ("dns", "servfail")):
            actions.append(
                {
                    "tool": "set_dns_override",
                    "args": {"hostname": "api.voice.local", "target": "secondary-resolver"},
                }
            )
            reply_parts.append("DNS timeouts match the 2025 resolver incident. Pinning critical hostnames.")
        if any(k in lower for k in ("stt", "tts", "429", "503")) or "provider" in lower:
            actions.append({"tool": "open_provider_circuit", "args": {"provider": "deepgram", "open_": True}})
            reply_parts.append("Opening the primary STT circuit and failing over to the secondary provider.")
        if any(k in lower for k in ("fiber", "cut")) or (
            "dfw" in lower and "bgp" not in lower and "flap" not in lower
        ):
            actions.append(
                {"tool": "shift_anycast_weight", "args": {"from_pop": "DFW-E", "to_pop": "DFW-W", "amount": 40}}
            )
            reply_parts.append("Treating this like the DFW metro cut: shift weight to the west ring.")
        if any(k in lower for k in ("simulate", "inject", "drill")):
            actions.append(
                {"tool": "simulate_fault", "args": {"pop": "AMS-1", "loss_pct": 30, "latency_ms": 150}}
            )
            reply_parts.append("Injecting a synthetic fault at AMS-1 for the drill.")
        if any(k in lower for k in ("heal", "restore", "recover")):
            actions.append({"tool": "heal_pop", "args": {"pop": "AMS-1"}})
            reply_parts.append("Restoring AMS-1 to healthy baseline.")

        seen: set[str] = set()
        uniq: list[dict[str, Any]] = []
        for a in actions:
            key = json.dumps(a, sort_keys=True)
            if key not in seen:
                seen.add(key)
                uniq.append(a)

        if not reply_parts:
            reply_parts.append(
                "I can check PoP status, dampen BGP peers, shift anycast, override DNS, "
                "or circuit-break STT/TTS/LLM providers. Tell me the symptom or say 'status'."
            )
            uniq.append({"tool": "get_network_status", "args": {}})

        lines = [f"ACTION: {json.dumps(a, separators=(',', ':'))}" for a in uniq]
        return "\n".join(lines + [""] + reply_parts)

    def _extract_actions(self, text: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for match in ACTION_LINE_RE.finditer(text):
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("tool"):
                found.append(payload)
        return found

    async def _run_actions(self, text: str, call_id: int | None) -> list[str]:
        used: list[str] = []
        for payload in self._extract_actions(text):
            tool = payload["tool"]
            args = payload.get("args") or {}
            await event_bus.emit(
                TransactionKind.mcp,
                f"MCP tool: {tool}",
                status="started",
                detail=json.dumps(args),
                provider="builtin-network",
                call_id=call_id,
            )
            started = time.perf_counter()
            result = await invoke_tool(tool, **args)
            latency = (time.perf_counter() - started) * 1000
            await event_bus.emit(
                TransactionKind.mcp,
                f"MCP {tool}: {'ok' if result.ok else 'failed'}",
                status="success" if result.ok else "failed",
                detail=result.summary,
                provider="builtin-network",
                call_id=call_id,
                latency_ms=latency,
                meta=result.data,
            )
            used.append(f"{tool}: {result.summary}")
        return used

    def _strip_actions(self, text: str) -> str:
        cleaned = ACTION_LINE_RE.sub("", text)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    async def handle_chat(
        self,
        session: AsyncSession,
        content: str,
        call_id: Optional[int] = None,
        channel: str = "chat",
    ) -> dict[str, Any]:
        if call_id:
            call = await session.get(CallRecord, call_id)
            if not call:
                raise ValueError("Call not found")
        else:
            call = await self.create_call(session, channel=channel)

        history_stmt = (
            select(Message).where(Message.call_id == call.id).order_by(Message.created_at.asc())
        )
        history = list((await session.execute(history_stmt)).scalars().all())

        user_msg = Message(call_id=call.id, role=MessageRole.user.value, content=content)
        session.add(user_msg)
        await session.commit()
        await session.refresh(user_msg)

        await event_bus.emit(
            TransactionKind.chat,
            "User message",
            status="info",
            detail=content[:240],
            call_id=call.id,
        )

        # RAG retrieval transaction
        rag_started = time.perf_counter()
        prompt, rag_snips = self._build_prompt(content, history)
        rag_latency = (time.perf_counter() - rag_started) * 1000
        rag_ev = await event_bus.emit(
            TransactionKind.rag,
            "RAG retrieval",
            status="success",
            detail=f"{len(rag_snips)} chunks",
            call_id=call.id,
            latency_ms=rag_latency,
            meta={"chunks": rag_snips},
        )
        await self._persist_tx(
            session,
            event_id=rag_ev.id,
            call_id=call.id,
            kind="rag",
            status="success",
            title="RAG retrieval",
            detail=f"{len(rag_snips)} chunks",
            latency_ms=rag_latency,
            meta={"chunks": rag_snips},
        )

        llm_cfgs = await providers_by_kind(session, "llm")

        async def make_slot(cfg):
            async def _call():
                return await self._llm_openai(cfg, prompt, content)

            label = f"{cfg.provider}:{cfg.model or 'default'}{':fb' if cfg.is_fallback else ''}"
            return ProviderSlot(name=label, call=_call, is_fallback=cfg.is_fallback, priority=cfg.priority)

        slots = [await make_slot(c) for c in llm_cfgs] or [
            ProviderSlot(name="demo-llm", call=lambda: self._demo_llm(content), is_fallback=False)
        ]

        async def on_event(kind: str, payload: dict[str, Any]):
            status_map = {
                "attempt": "started",
                "success": "success",
                "failed": "failed",
                "circuit_open": "retry",
            }
            status = status_map.get(kind, "info")
            if payload.get("fallback") and kind == "attempt":
                status = "fallback"
            if kind == "attempt" and payload.get("attempt", 1) > 1:
                status = "retry"
            ev = await event_bus.emit(
                TransactionKind.llm,
                f"LLM {kind}",
                status=status,
                detail=str(payload.get("detail", "")),
                provider=str(payload.get("provider", "")),
                call_id=call.id,
                latency_ms=payload.get("latency_ms"),
                meta=payload,
            )
            await self._persist_tx(
                session,
                event_id=ev.id,
                call_id=call.id,
                kind="llm",
                status=status,
                title=f"LLM {kind}",
                detail=str(payload.get("detail", "")),
                provider=str(payload.get("provider", "")),
                latency_ms=payload.get("latency_ms"),
                meta=payload,
            )

        try:
            result = await self.executor.execute(slots, attempts=settings.llm_retry_attempts, on_event=on_event)
            raw = result.value
        except Exception as exc:  # noqa: BLE001
            raw = (
                f"I could not reach any LLM provider ({exc}). "
                "Configure API keys in Providers, or continue in demo mode after adding a blank key."
            )
            # Force demo once
            raw = await self._demo_llm(content)

        tools_used = await self._run_actions(raw, call.id)
        spoken = self._strip_actions(raw)
        if tools_used:
            spoken = f"{spoken}\n\nMitigation results:\n- " + "\n- ".join(tools_used)

        asst = Message(
            call_id=call.id,
            role=MessageRole.assistant.value,
            content=spoken,
            meta={"tools": tools_used, "rag": rag_snips},
        )
        session.add(asst)
        await session.commit()
        await session.refresh(asst)

        await event_bus.emit(
            TransactionKind.chat,
            "Assistant reply",
            status="success",
            detail=spoken[:240],
            call_id=call.id,
        )

        # Simulated STT/TTS/VAD breadcrumbs for hybrid sessions (live TX panel)
        if channel in {"voice", "hybrid"}:
            await event_bus.emit(
                TransactionKind.vad,
                "VAD speech segment",
                status="success",
                provider="silero",
                call_id=call.id,
                detail="end-of-utterance",
            )
            await event_bus.emit(
                TransactionKind.stt,
                "STT transcript",
                status="success",
                provider="configured-primary",
                call_id=call.id,
                detail=content[:120],
            )
            await event_bus.emit(
                TransactionKind.tts,
                "TTS synthesis",
                status="success",
                provider="configured-primary",
                call_id=call.id,
                detail=f"{len(spoken)} chars",
            )

        return {
            "call": call,
            "user_message": user_msg,
            "assistant_message": asst,
            "rag_context": rag_snips,
            "tools_used": tools_used,
        }


agent_service = AgentService()
