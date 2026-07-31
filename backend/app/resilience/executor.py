from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

T = TypeVar("T")


class CircuitState(str, Enum):
    closed = "closed"
    open = "open"
    half_open = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3
    reset_seconds: float = 30.0
    failures: int = 0
    opened_at: float | None = None
    state: CircuitState = CircuitState.closed

    def allow(self) -> bool:
        if self.state == CircuitState.closed:
            return True
        if self.state == CircuitState.open and self.opened_at is not None:
            if time.monotonic() - self.opened_at >= self.reset_seconds:
                self.state = CircuitState.half_open
                return True
            return False
        return self.state == CircuitState.half_open

    def record_success(self) -> None:
        self.failures = 0
        self.state = CircuitState.closed
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold or self.state == CircuitState.half_open:
            self.state = CircuitState.open
            self.opened_at = time.monotonic()


@dataclass
class ProviderSlot:
    name: str
    call: Callable[..., Awaitable[Any]]
    is_fallback: bool = False
    priority: int = 0


@dataclass
class ResilienceResult:
    value: Any
    provider: str
    attempts: int
    used_fallback: bool = False
    errors: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


class ResilientExecutor:
    """Retries primary providers, then falls back; tracks circuit breakers."""

    def __init__(self, failure_threshold: int = 3, reset_seconds: float = 30.0) -> None:
        self._circuits: dict[str, CircuitBreaker] = {}
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self._lock = asyncio.Lock()

    def _circuit(self, name: str) -> CircuitBreaker:
        if name not in self._circuits:
            self._circuits[name] = CircuitBreaker(
                name=name,
                failure_threshold=self.failure_threshold,
                reset_seconds=self.reset_seconds,
            )
        return self._circuits[name]

    def status(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "state": c.state.value,
                "failures": c.failures,
                "opened_at": c.opened_at,
            }
            for name, c in self._circuits.items()
        }

    async def execute(
        self,
        slots: list[ProviderSlot],
        *args: Any,
        attempts: int = 3,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None] | None] | None = None,
        **kwargs: Any,
    ) -> ResilienceResult:
        ordered = sorted(slots, key=lambda s: (s.is_fallback, s.priority))
        errors: list[str] = []
        started = time.perf_counter()
        total_attempts = 0

        async def emit(kind: str, **payload: Any) -> None:
            if on_event:
                maybe = on_event(kind, payload)
                if asyncio.iscoroutine(maybe):
                    await maybe

        for slot in ordered:
            circuit = self._circuit(slot.name)
            if not circuit.allow():
                await emit(
                    "circuit_open",
                    provider=slot.name,
                    detail=f"Circuit open for {slot.name}",
                )
                errors.append(f"{slot.name}: circuit open")
                continue

            try:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(attempts),
                    wait=wait_exponential_jitter(initial=0.4, max=4),
                    retry=retry_if_exception_type(Exception),
                    reraise=True,
                ):
                    with attempt:
                        total_attempts += 1
                        await emit(
                            "attempt",
                            provider=slot.name,
                            attempt=attempt.retry_state.attempt_number,
                            fallback=slot.is_fallback,
                        )
                        value = await slot.call(*args, **kwargs)
                        circuit.record_success()
                        latency_ms = (time.perf_counter() - started) * 1000
                        await emit(
                            "success",
                            provider=slot.name,
                            latency_ms=latency_ms,
                            fallback=slot.is_fallback,
                        )
                        return ResilienceResult(
                            value=value,
                            provider=slot.name,
                            attempts=total_attempts,
                            used_fallback=slot.is_fallback,
                            errors=errors,
                            latency_ms=latency_ms,
                        )
            except Exception as exc:  # noqa: BLE001
                circuit.record_failure()
                msg = f"{slot.name}: {exc}"
                errors.append(msg)
                await emit("failed", provider=slot.name, detail=str(exc), fallback=slot.is_fallback)

        latency_ms = (time.perf_counter() - started) * 1000
        raise RuntimeError(
            f"All providers failed after {total_attempts} attempts: {'; '.join(errors)}"
        )
