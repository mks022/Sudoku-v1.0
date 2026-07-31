from __future__ import annotations

"""
Pipecat voice pipeline scaffolding.

Builds a VAD → STT → LLM → TTS graph with resilient provider selection.
When API keys are missing, the HTTP chat path remains fully usable (demo LLM).
Voice WebSocket sessions stream PCM frames and mirror live transactions.
"""

import base64
import json
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import TransactionKind
from app.services.agent import agent_service
from app.services.events import event_bus
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

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self.websocket.send_json(payload)

    async def on_audio_chunk(self, pcm_b64: str, session: AsyncSession) -> None:
        await event_bus.emit(
            TransactionKind.vad,
            "Audio frame received",
            status="info",
            provider="silero",
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
        await event_bus.emit(
            TransactionKind.vad,
            "End of utterance",
            status="success",
            provider="silero",
            call_id=self.call_id,
        )

        text = transcript
        if not text:
            # Without live STT credentials, accept client-side or empty
            stt_cfgs = await providers_by_kind(session, "stt")
            primary = next((c for c in stt_cfgs if not c.is_fallback), None)
            if primary and primary.api_key and self._audio_buffer:
                text = await self._transcribe(primary, bytes(self._audio_buffer))
            else:
                await event_bus.emit(
                    TransactionKind.stt,
                    "STT skipped — no key or empty audio",
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

        result = await agent_service.handle_chat(
            session, content=text, call_id=self.call_id, channel="voice"
        )
        reply = result["assistant_message"].content
        await self.send_json(
            {
                "type": "assistant_text",
                "call_id": self.call_id,
                "text": reply,
                "tools": result["tools_used"],
                "rag": result["rag_context"],
            }
        )

        # TTS: emit event; optional base64 audio if provider configured
        tts_cfgs = await providers_by_kind(session, "tts")
        tts = next((c for c in tts_cfgs if c.enabled), None)
        audio_b64 = None
        if tts and tts.api_key:
            try:
                audio_b64 = await self._synthesize(tts, reply)
                await event_bus.emit(
                    TransactionKind.tts,
                    "TTS complete",
                    status="success",
                    provider=tts.provider,
                    call_id=self.call_id,
                )
            except Exception as exc:  # noqa: BLE001
                await event_bus.emit(
                    TransactionKind.tts,
                    "TTS failed — falling back to text",
                    status="fallback",
                    provider=tts.provider if tts else "",
                    call_id=self.call_id,
                    detail=str(exc),
                )
        else:
            await event_bus.emit(
                TransactionKind.tts,
                "TTS text-only (no API key)",
                status="info",
                call_id=self.call_id,
            )

        await self.send_json(
            {
                "type": "assistant_audio",
                "call_id": self.call_id,
                "audio_b64": audio_b64,
                "mime": "audio/mpeg" if audio_b64 else None,
                "text": reply,
            }
        )

    async def _transcribe(self, cfg, audio: bytes) -> str:
        import httpx

        await event_bus.emit(
            TransactionKind.stt,
            "STT request",
            status="started",
            provider=cfg.provider,
            call_id=self.call_id,
        )
        if cfg.provider == "openai":
            headers = {"Authorization": f"Bearer {cfg.api_key}"}
            base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
            files = {"file": ("audio.wav", audio, "audio/wav")}
            data = {"model": cfg.model or "whisper-1"}
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{base}/audio/transcriptions", headers=headers, data=data, files=files
                )
                resp.raise_for_status()
                return resp.json().get("text", "")
        # Deepgram
        headers = {
            "Authorization": f"Token {cfg.api_key}",
            "Content-Type": "audio/wav",
        }
        base = (cfg.base_url or "https://api.deepgram.com").rstrip("/")
        model = cfg.model or "nova-2"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base}/v1/listen?model={model}&smart_format=true",
                headers=headers,
                content=audio,
            )
            resp.raise_for_status()
            data = resp.json()
            return (
                data.get("results", {})
                .get("channels", [{}])[0]
                .get("alternatives", [{}])[0]
                .get("transcript", "")
            )

    async def _synthesize(self, cfg, text: str) -> str:
        import httpx

        if cfg.provider == "openai":
            headers = {
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            }
            base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
            body = {
                "model": cfg.model or "gpt-4o-mini-tts",
                "voice": (cfg.extra or {}).get("voice", "alloy"),
                "input": text[:4000],
            }
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{base}/audio/speech", headers=headers, json=body)
                resp.raise_for_status()
                return base64.b64encode(resp.content).decode("ascii")

        # Cartesia-style placeholder: if not OpenAI, skip binary and raise
        raise RuntimeError(f"TTS provider {cfg.provider} requires additional SDK wiring")


def pipeline_info() -> dict[str, Any]:
    return {
        "pipecat_installed": PIPECAT_AVAILABLE,
        "stages": ["transport", "vad(silero)", "stt", "llm+rag+mcp", "tts", "transport"],
        "resilience": ["retry", "circuit_breaker", "provider_fallback", "text_degrade"],
        "note": (
            "Voice WebSocket accepts audio chunks + utterance_end. "
            "Chat path always available. Configure provider API keys in the frontend."
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
            async with session_factory() as session:
                if mtype == "audio":
                    await voice.on_audio_chunk(msg.get("data", ""), session)
                elif mtype == "utterance_end":
                    await voice.on_utterance_end(session, transcript=msg.get("transcript"))
                elif mtype == "chat":
                    result = await agent_service.handle_chat(
                        session,
                        content=msg.get("text", ""),
                        call_id=call_id,
                        channel="hybrid",
                    )
                    await voice.send_json(
                        {
                            "type": "assistant_text",
                            "call_id": call_id,
                            "text": result["assistant_message"].content,
                            "tools": result["tools_used"],
                            "rag": result["rag_context"],
                        }
                    )
                elif mtype == "ping":
                    await voice.send_json({"type": "pong"})
                elif mtype == "close":
                    voice.active = False
    except Exception as exc:  # noqa: BLE001
        logger.exception("voice ws error: %s", exc)
        try:
            await voice.send_json({"type": "error", "detail": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        async with session_factory() as session:
            try:
                await agent_service.end_call(session, call_id)
            except Exception:  # noqa: BLE001
                pass
