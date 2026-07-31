from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.mcp_tools.network import cleanup_pass_tools, invoke_tool_tracked, tools_prompt_block
from app.models.db import CallRecord, Message, TransactionLog
from app.models.schemas import MessageRole, TransactionKind
from app.rag.index import rag_index
from app.resilience.executor import ProviderSlot, ResilientExecutor
from app.services.events import event_bus
from app.services.pass_manager import PassAborted, TroubleshootingPass, pass_manager
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

    async def _llm_call(self, cfg, prompt: str, user_text: str) -> str:
        from app.services.adapters import call_llm

        return await call_llm(
            cfg,
            system=SYSTEM_PROMPT,
            prompt=prompt,
            user_text=user_text,
            demo_fn=self._demo_llm,
        )
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
            actions.append({"tool": "open_provider_circuit", "args": {"provider": "primary-stt", "open_": True}})
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

    async def _narrate(
        self,
        session: AsyncSession,
        *,
        call_id: int,
        text: str,
        phase: str,
        on_progress: Any | None = None,
        persist: bool = True,
        pass_obj: TroubleshootingPass | None = None,
    ) -> dict[str, Any]:
        """Push a speakable troubleshooting breadcrumb into the live conversation."""
        if pass_obj:
            pass_obj.checkpoint(phase)
        item = {
            "phase": phase,
            "text": text,
            "speak": True,
            "call_id": call_id,
            "ts": time.time(),
            "pass_id": pass_obj.pass_id if pass_obj else None,
        }
        await event_bus.emit(
            TransactionKind.narration,
            f"Narration · {phase}",
            status="info",
            detail=text,
            call_id=call_id,
            meta=item,
        )
        if persist:
            msg = Message(
                call_id=call_id,
                role=MessageRole.assistant.value,
                content=text,
                meta={"narration": True, "phase": phase, "speak": True, "pass_id": item["pass_id"]},
            )
            session.add(msg)
            await session.commit()
            await session.refresh(msg)
            item["message_id"] = msg.id
        if on_progress:
            maybe = on_progress(item)
            if asyncio.iscoroutine(maybe):
                await maybe
        return item

    def _tool_start_line(self, tool: str, args: dict[str, Any]) -> str:
        if tool == "get_network_status":
            region = args.get("region")
            return (
                f"Checking live PoP health{f' in {region}' if region else ''} now."
            )
        if tool == "dampen_bgp_peer":
            return f"Dampening BGP peer {args.get('peer', 'target')} for {args.get('minutes', 15)} minutes."
        if tool == "shift_anycast_weight":
            return (
                f"Shifting {args.get('amount', 25)} anycast weight "
                f"from {args.get('from_pop')} to {args.get('to_pop')}."
            )
        if tool == "open_provider_circuit":
            state = "Opening" if args.get("open_", True) else "Closing"
            return f"{state} the circuit breaker for provider {args.get('provider')}."
        if tool == "set_dns_override":
            return f"Pinning DNS for {args.get('hostname')} to {args.get('target')}."
        if tool == "simulate_fault":
            return f"Injecting a synthetic fault at {args.get('pop')} for the drill."
        if tool == "heal_pop":
            return f"Restoring {args.get('pop')} to a healthy baseline."
        return f"Running mitigation tool {tool}."

    async def _run_actions(
        self,
        session: AsyncSession,
        text: str,
        call_id: int | None,
        on_progress: Any | None = None,
        pass_obj: TroubleshootingPass | None = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        used: list[str] = []
        narrations: list[dict[str, Any]] = []
        actions = self._extract_actions(text)
        if not actions or call_id is None:
            return used, narrations

        if pass_obj:
            pass_obj.checkpoint("mcp_plan")

        if len(actions) > 1:
            n = await self._narrate(
                session,
                call_id=call_id,
                text=f"I have {len(actions)} mitigation steps. I'll talk through each as I go.",
                phase="plan",
                on_progress=on_progress,
                pass_obj=pass_obj,
            )
            narrations.append(n)

        for payload in actions:
            if pass_obj:
                pass_obj.checkpoint(f"mcp:{payload.get('tool')}")
            tool = payload["tool"]
            args = payload.get("args") or {}
            start_line = self._tool_start_line(tool, args)
            n = await self._narrate(
                session,
                call_id=call_id,
                text=start_line,
                phase=f"tool_start:{tool}",
                on_progress=on_progress,
                pass_obj=pass_obj,
            )
            narrations.append(n)

            await event_bus.emit(
                TransactionKind.mcp,
                f"MCP tool: {tool}",
                status="started",
                detail=json.dumps(args),
                provider="builtin-network",
                call_id=call_id,
            )
            started = time.perf_counter()
            try:
                if pass_obj:
                    result = await pass_obj.run(
                        invoke_tool_tracked(
                            call_id, tool, pass_id=pass_obj.pass_id, **args
                        )
                    )
                else:
                    result = await invoke_tool_tracked(call_id, tool, **args)
            except PassAborted:
                await event_bus.emit(
                    TransactionKind.mcp,
                    f"MCP {tool}: aborted",
                    status="failed",
                    detail="Operator superseded this approach",
                    provider="builtin-network",
                    call_id=call_id,
                )
                if pass_obj:
                    await cleanup_pass_tools(call_id, pass_id=pass_obj.pass_id)
                raise
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
            if result.data.get("aborted"):
                n = await self._narrate(
                    session,
                    call_id=call_id,
                    text=f"Stopped {tool} — switching approaches.",
                    phase=f"tool_abort:{tool}",
                    on_progress=on_progress,
                    pass_obj=pass_obj,
                )
                narrations.append(n)
                break
            result_line = (
                result.summary
                if result.ok
                else f"That step failed: {result.summary}. We can try another path."
            )
            n = await self._narrate(
                session,
                call_id=call_id,
                text=result_line,
                phase=f"tool_result:{tool}",
                on_progress=on_progress,
                pass_obj=pass_obj,
            )
            narrations.append(n)
            await asyncio.sleep(0.12)

        return used, narrations

    def _strip_actions(self, text: str) -> str:
        cleaned = ACTION_LINE_RE.sub("", text)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    async def handle_chat(
        self,
        session: AsyncSession,
        content: str,
        call_id: Optional[int] = None,
        channel: str = "chat",
        on_progress: Any | None = None,
        abort_current: bool = True,
        supersede_reason: str = "operator_new_approach",
    ) -> dict[str, Any]:
        if call_id:
            call = await session.get(CallRecord, call_id)
            if not call:
                raise ValueError("Call not found")
        else:
            call = await self.create_call(session, channel=channel)

        abort_info: dict[str, Any] | None = None
        narrations: list[dict[str, Any]] = []
        if abort_current and pass_manager.is_active(call.id):
            # Announce abort before tearing down so the operator hears the switch.
            await event_bus.emit(
                TransactionKind.system,
                "Aborting active MCP pass",
                status="info",
                detail=supersede_reason,
                call_id=call.id,
            )
            abort_info = await pass_manager.abort(call.id, reason=supersede_reason, wait=True)
            # Only clean tools belonging to the aborted pass, not a successor.
            aborted_pass_id = abort_info.get("pass_id")
            tool_cleanup = await cleanup_pass_tools(call.id, pass_id=aborted_pass_id)
            if abort_info.get("aborted"):
                abort_info["mcp_cleanup"] = tool_cleanup
                n = await self._narrate(
                    session,
                    call_id=call.id,
                    text=(
                        "Understood — aborting the current mitigation pass, "
                        "closing MCP tool threads, then starting your new approach."
                    ),
                    phase="abort",
                    on_progress=on_progress,
                )
                narrations.append(n)

        pass_obj, begin_abort = await pass_manager.begin(
            call.id,
            abort_existing=abort_current,
            reason=supersede_reason,
            meta={"channel": channel, "content": content[:120]},
        )
        if begin_abort and not abort_info:
            abort_info = begin_abort

        history_stmt = (
            select(Message).where(Message.call_id == call.id).order_by(Message.created_at.asc())
        )
        history = list((await session.execute(history_stmt)).scalars().all())

        user_msg = Message(
            call_id=call.id,
            role=MessageRole.user.value,
            content=content,
            meta={"pass_id": pass_obj.pass_id, "supersede": bool(abort_info)},
        )
        session.add(user_msg)
        await session.commit()
        await session.refresh(user_msg)

        await event_bus.emit(
            TransactionKind.chat,
            "User message",
            status="info",
            detail=content[:240],
            call_id=call.id,
            meta={"pass_id": pass_obj.pass_id},
        )

        aborted = False
        tools_used: list[str] = []
        rag_snips: list[str] = []
        chat_content = ""

        try:
            pass_obj.checkpoint("start")
            n = await self._narrate(
                session,
                call_id=call.id,
                text=(
                    "Starting a fresh MCP agent on your new approach — I'll narrate as I go."
                    if abort_info
                    else "On it — I'll troubleshoot this live and keep you posted as I go."
                ),
                phase="start",
                on_progress=on_progress,
                pass_obj=pass_obj,
            )
            narrations.append(n)

            rag_started = time.perf_counter()
            prompt, rag_snips = self._build_prompt(content, history)
            rag_latency = (time.perf_counter() - rag_started) * 1000
            pass_obj.checkpoint("rag")
            rag_ev = await event_bus.emit(
                TransactionKind.rag,
                "RAG retrieval",
                status="success",
                detail=f"{len(rag_snips)} chunks",
                call_id=call.id,
                latency_ms=rag_latency,
                meta={"chunks": rag_snips, "pass_id": pass_obj.pass_id},
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

            if rag_snips:
                n = await self._narrate(
                    session,
                    call_id=call.id,
                    text=(
                        f"I found {len(rag_snips)} related outage or runbook notes. "
                        f"Top match: {rag_snips[0].split(':')[0][:80]}."
                    ),
                    phase="rag",
                    on_progress=on_progress,
                    pass_obj=pass_obj,
                )
            else:
                n = await self._narrate(
                    session,
                    call_id=call.id,
                    text="No close historical match — I'll lean on live status and standard playbooks.",
                    phase="rag",
                    on_progress=on_progress,
                    pass_obj=pass_obj,
                )
            narrations.append(n)

            llm_cfgs = await providers_by_kind(session, "llm")

            async def make_slot(cfg):
                async def _call():
                    return await self._llm_call(cfg, prompt, content)

                label = f"{cfg.provider}:{cfg.model or 'default'}{':fb' if cfg.is_fallback else ''}"
                return ProviderSlot(name=label, call=_call, is_fallback=cfg.is_fallback, priority=cfg.priority)

            slots = [await make_slot(c) for c in llm_cfgs] or [
                ProviderSlot(name="demo-llm", call=lambda: self._demo_llm(content), is_fallback=False)
            ]

            async def on_event(kind: str, payload: dict[str, Any]):
                pass_obj.checkpoint("llm")
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
                if status in {"retry", "fallback"}:
                    reason = (
                        "failing over to a backup provider"
                        if status == "fallback"
                        else "retrying the model"
                    )
                    nn = await self._narrate(
                        session,
                        call_id=call.id,
                        text=f"Hit a provider snag — {reason}.",
                        phase=f"llm_{status}",
                        on_progress=on_progress,
                        pass_obj=pass_obj,
                    )
                    narrations.append(nn)
                ev = await event_bus.emit(
                    TransactionKind.llm,
                    f"LLM {kind}",
                    status=status,
                    detail=str(payload.get("detail", "")),
                    provider=str(payload.get("provider", "")),
                    call_id=call.id,
                    latency_ms=payload.get("latency_ms"),
                    meta={**payload, "pass_id": pass_obj.pass_id},
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

            n = await self._narrate(
                session,
                call_id=call.id,
                text="Thinking through the mitigation plan.",
                phase="llm",
                on_progress=on_progress,
                pass_obj=pass_obj,
            )
            narrations.append(n)

            try:
                result = await pass_obj.run(
                    self.executor.execute(
                        slots, attempts=settings.llm_retry_attempts, on_event=on_event
                    )
                )
                raw = result.value
            except PassAborted:
                raise
            except Exception as exc:  # noqa: BLE001
                _ = exc
                raw = await self._demo_llm(content)
                nn = await self._narrate(
                    session,
                    call_id=call.id,
                    text="Primary LLM path failed — continuing with the local demo playbook.",
                    phase="llm_fallback",
                    on_progress=on_progress,
                    pass_obj=pass_obj,
                )
                narrations.append(nn)

            tools_used, tool_narrations = await self._run_actions(
                session, raw, call.id, on_progress=on_progress, pass_obj=pass_obj
            )
            narrations.extend(tool_narrations)

            spoken = self._strip_actions(raw)
            if not spoken:
                spoken = "Troubleshooting pass complete."
            if tools_used:
                wrap = (
                    "That wraps this pass. You can ask me to verify status, undo a step, or continue."
                )
                chat_content = f"{spoken}\n\nMitigation results:\n- " + "\n- ".join(tools_used)
            else:
                wrap = spoken
                chat_content = spoken

            n = await self._narrate(
                session,
                call_id=call.id,
                text=wrap if tools_used else f"Here's my read: {spoken}",
                phase="wrap",
                on_progress=on_progress,
                persist=False,
                pass_obj=pass_obj,
            )
            narrations.append(n)
            await pass_manager.finish(pass_obj, status="completed")

        except PassAborted as exc:
            aborted = True
            await cleanup_pass_tools(call.id, pass_id=pass_obj.pass_id)
            chat_content = (
                f"Previous approach aborted ({exc.reason}). "
                "Waiting for — or already starting — your new direction."
            )
            # Avoid duplicate abort chatter if a newer pass already owns the call.
            current = pass_manager.get(call.id)
            if current is None or current is pass_obj:
                n = await self._narrate(
                    session,
                    call_id=call.id,
                    text="Stopped the prior MCP agent cleanly. Ready for the new approach.",
                    phase="aborted",
                    on_progress=on_progress,
                    persist=True,
                )
                narrations.append(n)
            await pass_manager.finish(pass_obj, status="aborted")
            await event_bus.emit(
                TransactionKind.system,
                "MCP pass aborted",
                status="failed",
                detail=exc.reason,
                call_id=call.id,
                meta={"pass_id": pass_obj.pass_id},
            )

        asst = Message(
            call_id=call.id,
            role=MessageRole.assistant.value,
            content=chat_content,
            meta={
                "tools": tools_used,
                "rag": rag_snips,
                "narrations": narrations,
                "pass_id": pass_obj.pass_id,
                "aborted": aborted,
                "abort_info": abort_info,
            },
        )
        session.add(asst)
        await session.commit()
        await session.refresh(asst)

        await event_bus.emit(
            TransactionKind.chat,
            "Assistant reply" if not aborted else "Pass aborted",
            status="success" if not aborted else "failed",
            detail=chat_content[:240],
            call_id=call.id,
        )

        if channel in {"voice", "hybrid"} and not aborted:
            vad = (await providers_by_kind(session, "vad") or [None])[0]
            stt = (await providers_by_kind(session, "stt") or [None])[0]
            tts = (await providers_by_kind(session, "tts") or [None])[0]
            await event_bus.emit(
                TransactionKind.vad,
                "VAD speech segment",
                status="success",
                provider=(vad.provider if vad else "unconfigured"),
                call_id=call.id,
                detail="end-of-utterance",
            )
            await event_bus.emit(
                TransactionKind.stt,
                "STT transcript",
                status="success",
                provider=(stt.provider if stt else "unconfigured"),
                call_id=call.id,
                detail=content[:120],
            )
            await event_bus.emit(
                TransactionKind.tts,
                "TTS synthesis",
                status="success",
                provider=(tts.provider if tts else "unconfigured"),
                call_id=call.id,
                detail=f"{len(chat_content)} chars",
            )

        return {
            "call": call,
            "user_message": user_msg,
            "assistant_message": asst,
            "rag_context": rag_snips,
            "tools_used": tools_used,
            "narrations": narrations,
            "aborted": aborted,
            "abort_info": abort_info,
            "pass_id": pass_obj.pass_id,
        }


agent_service = AgentService()
