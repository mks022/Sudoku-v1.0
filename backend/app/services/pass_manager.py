"""Per-call troubleshooting pass lifecycle: abort, join, restart."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class PassAborted(Exception):
    """Raised when an operator supersedes the active MCP troubleshooting pass."""

    def __init__(self, call_id: int, pass_id: str, reason: str = "aborted") -> None:
        self.call_id = call_id
        self.pass_id = pass_id
        self.reason = reason
        super().__init__(f"pass {pass_id} on call {call_id} {reason}")


@dataclass
class TroubleshootingPass:
    pass_id: str
    call_id: int
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: set[asyncio.Task] = field(default_factory=set)
    phase: str = "starting"
    reason: str = ""
    started_at: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)

    def aborted(self) -> bool:
        return self.cancel.is_set()

    def checkpoint(self, phase: str | None = None) -> None:
        if phase:
            self.phase = phase
        if self.cancel.is_set():
            raise PassAborted(self.call_id, self.pass_id, self.reason or "aborted")

    def track(self, task: asyncio.Task) -> asyncio.Task:
        self.tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            self.tasks.discard(t)

        task.add_done_callback(_done)
        return task

    async def run(self, coro):
        """Run a coroutine as a tracked child task; respect abort."""
        self.checkpoint()
        task = asyncio.create_task(coro)
        self.track(task)
        try:
            return await task
        except asyncio.CancelledError as exc:
            raise PassAborted(self.call_id, self.pass_id, self.reason or "cancelled") from exc


class PassManager:
    """One active MCP troubleshooting pass per call; abort closes children then allows restart."""

    def __init__(self, join_timeout: float = 3.0) -> None:
        self._active: dict[int, TroubleshootingPass] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self.join_timeout = join_timeout

    def _lock_for(self, call_id: int) -> asyncio.Lock:
        if call_id not in self._locks:
            self._locks[call_id] = asyncio.Lock()
        return self._locks[call_id]

    def get(self, call_id: int) -> TroubleshootingPass | None:
        return self._active.get(call_id)

    def is_active(self, call_id: int) -> bool:
        p = self._active.get(call_id)
        return bool(p and not p.cancel.is_set())

    async def abort(
        self,
        call_id: int,
        *,
        reason: str = "operator_supersede",
        wait: bool = True,
    ) -> dict[str, Any]:
        async with self._lock_for(call_id):
            return await self._abort_unlocked(call_id, reason=reason, wait=wait)

    async def _abort_unlocked(
        self,
        call_id: int,
        *,
        reason: str,
        wait: bool,
    ) -> dict[str, Any]:
        current = self._active.get(call_id)
        if not current:
            return {"aborted": False, "call_id": call_id, "reason": reason}

        current.reason = reason
        current.cancel.set()
        tasks = list(current.tasks)
        for task in tasks:
            if not task.done():
                task.cancel()

        closed = 0
        timed_out = False
        if wait and tasks:
            done, pending = await asyncio.wait(tasks, timeout=self.join_timeout)
            closed = len(done)
            if pending:
                timed_out = True
                for task in pending:
                    task.cancel()
                await asyncio.wait(pending, timeout=1.0)

        if self._active.get(call_id) is current:
            del self._active[call_id]

        return {
            "aborted": True,
            "call_id": call_id,
            "pass_id": current.pass_id,
            "reason": reason,
            "tasks_closed": closed,
            "tasks_total": len(tasks),
            "timed_out": timed_out,
            "phase_at_abort": current.phase,
            "elapsed_ms": (time.time() - current.started_at) * 1000,
        }

    async def begin(
        self,
        call_id: int,
        *,
        abort_existing: bool = True,
        reason: str = "operator_supersede",
        meta: dict[str, Any] | None = None,
    ) -> tuple[TroubleshootingPass, dict[str, Any] | None]:
        """Start a new pass, optionally aborting any in-flight MCP agent first."""
        abort_info: dict[str, Any] | None = None
        async with self._lock_for(call_id):
            if abort_existing and call_id in self._active:
                abort_info = await self._abort_unlocked(call_id, reason=reason, wait=True)

            pass_obj = TroubleshootingPass(
                pass_id=str(uuid.uuid4()),
                call_id=call_id,
                meta=meta or {},
            )
            self._active[call_id] = pass_obj
            return pass_obj, abort_info

    async def finish(self, pass_obj: TroubleshootingPass, *, status: str = "completed") -> None:
        async with self._lock_for(pass_obj.call_id):
            current = self._active.get(pass_obj.call_id)
            if current is pass_obj:
                del self._active[pass_obj.call_id]
            pass_obj.meta["status"] = status

    def status(self, call_id: int | None = None) -> dict[str, Any]:
        if call_id is not None:
            p = self._active.get(call_id)
            if not p:
                return {"active": False, "call_id": call_id}
            return {
                "active": not p.cancel.is_set(),
                "call_id": call_id,
                "pass_id": p.pass_id,
                "phase": p.phase,
                "tasks": len(p.tasks),
                "elapsed_ms": (time.time() - p.started_at) * 1000,
            }
        return {
            "active_passes": [
                {
                    "call_id": p.call_id,
                    "pass_id": p.pass_id,
                    "phase": p.phase,
                    "tasks": len(p.tasks),
                    "aborted": p.cancel.is_set(),
                }
                for p in self._active.values()
            ]
        }


pass_manager = PassManager()
