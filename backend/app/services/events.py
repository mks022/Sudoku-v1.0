from __future__ import annotations

import asyncio
import uuid
from collections import deque
from datetime import datetime
from typing import Any

from app.models.schemas import TransactionEvent, TransactionKind


class EventBus:
    """Fan-out live transaction events to WebSocket subscribers."""

    def __init__(self, history_size: int = 200) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._history: deque[TransactionEvent] = deque(maxlen=history_size)
        self._lock = asyncio.Lock()

    def history(self) -> list[TransactionEvent]:
        return list(self._history)

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def publish(self, event: TransactionEvent) -> TransactionEvent:
        self._history.append(event)
        async with self._lock:
            dead: list[asyncio.Queue] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                self._subscribers.discard(q)
        return event

    async def emit(
        self,
        kind: TransactionKind | str,
        title: str,
        status: str = "info",
        detail: str = "",
        provider: str = "",
        call_id: int | None = None,
        latency_ms: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> TransactionEvent:
        event = TransactionEvent(
            id=str(uuid.uuid4()),
            call_id=call_id,
            kind=TransactionKind(kind) if isinstance(kind, str) else kind,
            status=status,
            title=title,
            detail=detail,
            provider=provider,
            latency_ms=latency_ms,
            created_at=datetime.utcnow(),
            meta=meta or {},
        )
        return await self.publish(event)


event_bus = EventBus()
