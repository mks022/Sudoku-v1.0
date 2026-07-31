from __future__ import annotations

"""
Pipecat voice pipeline scaffolding.

Builds a VAD → STT → LLM → TTS graph with resilient provider selection.
When API keys are missing, the HTTP chat path remains fully usable (demo LLM).
Voice WebSocket sessions stream PCM frames and mirror live transactions.
"""

import asyncio
import base64
import json
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp_tools.network import cleanup_pass_tools
from app.models.schemas import TransactionKind
from app.services.agent import agent_service
from app.services.events import event_bus
from app.services.pass_manager import pass_manager
from app.services.providers import providers_by_kind

logger = logging.getLogger(__name__)

PIPECAT_AVAILABLE = False
try:
    import pipecat  # noqa: F401

    PIPECAT_AVAILABLE = True
except Exception:  # noqa: BLE001
    PIPECAT_AVAILABLE = False


class VoiceSession:
    """Manages one hybrid voice/chat session over WebSocket."""

    def __init__(self, call_id: int, websocket: Any) -> None:
        self.call_id = call_id
        self.websocket = websocket
        self.active = True
        self._audio_buffer = bytearray()
        self._run_task: asyncio.Task | None = None

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self.websocket.send_json(payload)

    async def cancel_active_run(self, reason: str = "operator_new_approach") -> dict[str, Any]:
        """Gracefully stop the in-flight MCP agent + child tasks for this call."""
        info = await pass_manager.abort(self.call_id, reason=reason, wait=True)
        tool_cleanup = await cleanup_pass_tools(
            self.call_id, pass_id=info.get("pass_id") if info.get("aborted") else None
        )
        info["mcp_cleanup"] = tool_cleanup
        task = self._run_task
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=6.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
        self._run_task = None
        await self.send_json(
            {
                "type": "pass_aborted",
                "call_id": self.call_id,
                "reason": reason,
                "abort_info": info,
            }
        )
        return info

    def spawn(self, coro) -> asyncio.Task:
        """Replace any running pass task with a new one."""
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()
        task = asyncio.create_task(coro)
        self._run_task = task
        return task

    async def on_audio_chunk(self, pcm_b64: str, session: AsyncSession) -> None:
        vad_cfgs = await providers_by_kind(session, "vad")
        vad_name = vad_cfgs[0].provider if vad_cfgs else "unconfigured"
        await event_bus.emit(
            TransactionKind.vad,
            "Audio frame received",
            status="info",
            provider=vad_name,
            call_id=self.call_id,
            detail=f"{len(pcm_b64)} b64 chars",
        )
        try:
            raw = base64.b64decode(pcm_b64)
            self._audio_buffer.extend(raw)
        except Exception as exc:  # noqa: BLE001
            await event_bus.emit(
                TransactionKind.network,
                "Audio decode fault",
                status="failed",
                call_id=self.call_id,
                detail=str(exc),
            )
            return

        # Endpointing heuristic: client should send {"type":"utterance_end"}
        # For continuous streams we keep buffering until end signal.

    async def on_utterance_end(self, session: AsyncSession, transcript: Optional[str] = None) -> None:
        vad_cfgs = await providers_by_kind(session, "vad")
        await event_bus.emit(
            TransactionKind.vad,
            "End of utterance",
            status="success",
            provider=(vad_cfgs[0].provider if vad_cfgs else "unconfigured"),
            call_id=self.call_id,
        )

        text = transcript
        if not text:
            stt_cfgs = await providers_by_kind(session, "stt")
            primary = next((c for c in stt_cfgs if not c.is_fallback), None)
            needs_key = True
            if primary:
                auth = str((primary.extra or {}).get("auth_scheme") or "bearer").lower()
                needs_key = auth not in {"none"}
            if primary and self._audio_buffer and (primary.api_key or not needs_key):
                text = await self._transcribe(primary, bytes(self._audio_buffer))
            else:
                await event_bus.emit(
                    TransactionKind.stt,
                    "STT skipped — no provider/key or empty audio",
                    status="info",
                    provider=(primary.provider if primary else "none"),
                    call_id=self.call_id,
                    detail="Use chat box or send transcript field with utterance_end",
                )
                self._audio_buffer.clear()
                return

        self._audio_buffer.clear()
        await event_bus.emit(
            TransactionKind.stt,
            "STT complete",
            status="success",
            call_id=self.call_id,
            detail=text[:160],
        )

        async def on_progress(item: dict[str, Any]) -> None:
            text = str(item.get("text") or "")
            await self.send_json(
                {
                    "type": "narration",
                    "call_id": self.call_id,
                    "phase": item.get("phase"),
                    "text": text,
                    "speak": True,
                    "message_id": item.get("message_id"),
                }
            )
            # Speak each breadcrumb while troubleshooting is in flight
            tts_cfgs = await providers_by_kind(session, "tts")
            tts = next((c for c in tts_cfgs if c.enabled), None)
            can_tts = False
            if tts:
                auth = str((tts.extra or {}).get("auth_scheme") or "bearer").lower()
                can_tts = bool(tts.api_key) or auth == "none"
            audio_b64 = None
            if tts and can_tts and text:
                try:
                    audio_b64 = await self._synthesize(tts, text)
                    await event_bus.emit(
                        TransactionKind.tts,
                        "TTS narration step",
                        status="success",
                        provider=tts.provider,
                        call_id=self.call_id,
                        detail=item.get("phase"),
                    )
                except Exception as exc:  # noqa: BLE001
                    await event_bus.emit(
                        TransactionKind.tts,
                        "TTS narration failed",
                        status="fallback",
                        provider=tts.provider,
                        call_id=self.call_id,
                        detail=str(exc),
                    )
            await self.send_json(
                {
                    "type": "narration_audio",
                    "call_id": self.call_id,
                    "phase": item.get("phase"),
                    "text": text,
                    "audio_b64": audio_b64,
                    "mime": "audio/mpeg" if audio_b64 else None,
                }
            )

        result = await agent_service.handle_chat(
            session,
            content=text,
            call_id=self.call_id,
            channel="voice",
            on_progress=on_progress,
            abort_current=True,
            supersede_reason="operator_new_approach",
        )
        if result.get("aborted"):
            await self.send_json(
                {
                    "type": "pass_aborted",
                    "call_id": self.call_id,
                    "abort_info": result.get("abort_info"),
                    "text": result["assistant_message"].content,
                }
            )
            return
        reply = result["assistant_message"].content
        await self.send_json(
            {
                "type": "assistant_text",
                "call_id": self.call_id,
                "text": reply,
                "tools": result["tools_used"],
                "rag": result["rag_context"],
                "narrations": result.get("narrations") or [],
                "pass_id": result.get("pass_id"),
                "speak": False,
            }
        )

        await self.send_json(
            {
                "type": "assistant_audio",
                "call_id": self.call_id,
                "audio_b64": None,
                "mime": None,
                "text": reply,
                "note": "Step narrations were spoken live; final summary is text-only to avoid repeat.",
            }
        )

    async def _transcribe(self, cfg, audio: bytes) -> str:
        from app.services.adapters import call_stt

        await event_bus.emit(
            TransactionKind.stt,
            "STT request",
            status="started",
            provider=cfg.provider,
            call_id=self.call_id,
        )
        return await call_stt(cfg, audio)

    async def _synthesize(self, cfg, text: str) -> str:
        from app.services.adapters import call_tts

        return await call_tts(cfg, text)


def pipeline_info() -> dict[str, Any]:
    return {
        "pipecat_installed": PIPECAT_AVAILABLE,
        "stages": ["transport", "vad", "stt", "llm+rag+mcp", "tts", "transport"],
        "resilience": ["retry", "circuit_breaker", "provider_fallback", "text_degrade"],
        "note": (
            "Providers are protocol-driven. Troubleshooting narrations stream live to chat/TTS "
            "while mitigation runs; operators can keep talking mid-pass."
        ),
    }


async def handle_voice_ws(websocket: Any, session_factory, call_id: int) -> None:
    await websocket.accept()
    voice = VoiceSession(call_id, websocket)
    await voice.send_json(
        {
            "type": "session_ready",
            "call_id": call_id,
            "pipeline": pipeline_info(),
        }
    )
    try:
        while voice.active:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "ping":
                await voice.send_json({"type": "pong"})
                continue
            if mtype == "close":
                await voice.cancel_active_run(reason="session_close")
                voice.active = False
                continue
            if mtype == "abort":
                info = await voice.cancel_active_run(
                    reason=str(msg.get("reason") or "operator_abort")
                )
                await voice.send_json({"type": "abort_ack", "abort_info": info})
                continue

            if mtype in {"chat", "abort_and_chat"}:
                text = msg.get("text", "")
                reason = str(msg.get("reason") or "operator_new_approach")

                async def run_chat(
                    chat_text: str,
                    channel: str = "hybrid",
                    supersede_reason: str = reason,
                ) -> None:
                    async with session_factory() as session:
                        async def on_progress(item: dict[str, Any]) -> None:
                            await voice.send_json(
                                {
                                    "type": "narration",
                                    "call_id": call_id,
                                    "phase": item.get("phase"),
                                    "text": item.get("text"),
                                    "speak": True,
                                    "message_id": item.get("message_id"),
                                    "pass_id": item.get("pass_id"),
                                }
                            )

                        result = await agent_service.handle_chat(
                            session,
                            content=chat_text,
                            call_id=call_id,
                            channel=channel,
                            on_progress=on_progress,
                            abort_current=True,
                            supersede_reason=supersede_reason,
                        )
                        if result.get("aborted"):
                            await voice.send_json(
                                {
                                    "type": "pass_aborted",
                                    "call_id": call_id,
                                    "abort_info": result.get("abort_info"),
                                    "text": result["assistant_message"].content,
                                }
                            )
                            return
                        await voice.send_json(
                            {
                                "type": "assistant_text",
                                "call_id": call_id,
                                "text": result["assistant_message"].content,
                                "tools": result["tools_used"],
                                "rag": result["rag_context"],
                                "narrations": result.get("narrations") or [],
                                "pass_id": result.get("pass_id"),
                                "speak": False,
                            }
                        )

                if mtype == "abort_and_chat" or pass_manager.is_active(call_id):
                    await voice.cancel_active_run(reason=reason)
                voice.spawn(run_chat(text, "hybrid", reason))
            elif mtype == "utterance_end":
                if pass_manager.is_active(call_id):
                    await voice.cancel_active_run(reason="operator_new_approach")

                async def run_utterance() -> None:
                    async with session_factory() as session:
                        await voice.on_utterance_end(session, transcript=msg.get("transcript"))

                voice.spawn(run_utterance())
            elif mtype == "audio":
                async with session_factory() as session:
                    await voice.on_audio_chunk(msg.get("data", ""), session)
    except Exception as exc:  # noqa: BLE001
        logger.exception("voice ws error: %s", exc)
        try:
            await voice.send_json({"type": "error", "detail": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        await voice.cancel_active_run(reason="session_close")
        async with session_factory() as session:
            try:
                await agent_service.end_call(session, call_id)
            except Exception:  # noqa: BLE001
                pass
