"""MCP-style network mitigation tools exposed to the LLM agent."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolResult:
    name: str
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class NetworkState:
    """Simulated network control plane for demo + local mitigation drills."""

    peers: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "AMS-1": {"status": "up", "loss_pct": 0.2, "latency_ms": 18},
            "AMS-2": {"status": "up", "loss_pct": 0.1, "latency_ms": 17},
            "DFW-E": {"status": "up", "loss_pct": 0.3, "latency_ms": 28},
            "DFW-W": {"status": "up", "loss_pct": 0.2, "latency_ms": 27},
        }
    )
    anycast_weights: dict[str, int] = field(
        default_factory=lambda: {"AMS-1": 50, "AMS-2": 50, "DFW-E": 50, "DFW-W": 50}
    )
    circuits_open: dict[str, bool] = field(default_factory=dict)
    dns_overrides: dict[str, str] = field(default_factory=dict)
    incident_log: list[dict[str, Any]] = field(default_factory=list)


state = NetworkState()


def _log(action: str, detail: str, **extra: Any) -> None:
    state.incident_log.append(
        {"ts": time.time(), "action": action, "detail": detail, **extra}
    )
    if len(state.incident_log) > 200:
        state.incident_log = state.incident_log[-200:]


async def get_network_status(region: str | None = None) -> ToolResult:
    await asyncio.sleep(0.05)
    peers = state.peers
    if region:
        peers = {k: v for k, v in peers.items() if region.upper() in k.upper()}
    return ToolResult(
        name="get_network_status",
        ok=True,
        summary=f"Status for {len(peers)} PoPs",
        data={"peers": peers, "anycast_weights": state.anycast_weights},
    )


async def dampen_bgp_peer(peer: str, minutes: int = 15) -> ToolResult:
    await asyncio.sleep(0.08)
    key = peer.upper()
    if key not in state.peers:
        return ToolResult(name="dampen_bgp_peer", ok=False, summary=f"Unknown peer {peer}")
    state.peers[key]["status"] = "dampened"
    state.peers[key]["dampen_minutes"] = minutes
    _log("dampen_bgp_peer", f"Dampened {key} for {minutes}m", peer=key)
    return ToolResult(
        name="dampen_bgp_peer",
        ok=True,
        summary=f"Dampened BGP peer {key} for {minutes} minutes",
        data={"peer": key, "minutes": minutes},
    )


async def shift_anycast_weight(from_pop: str, to_pop: str, amount: int = 25) -> ToolResult:
    await asyncio.sleep(0.08)
    src, dst = from_pop.upper(), to_pop.upper()
    if src not in state.anycast_weights or dst not in state.anycast_weights:
        return ToolResult(name="shift_anycast_weight", ok=False, summary="Unknown PoP")
    move = min(amount, state.anycast_weights[src])
    state.anycast_weights[src] -= move
    state.anycast_weights[dst] += move
    _log("shift_anycast_weight", f"Moved {move} weight {src}→{dst}", from_pop=src, to_pop=dst)
    return ToolResult(
        name="shift_anycast_weight",
        ok=True,
        summary=f"Shifted {move} anycast weight from {src} to {dst}",
        data={"weights": dict(state.anycast_weights)},
    )


async def open_provider_circuit(provider: str, open_: bool = True) -> ToolResult:
    await asyncio.sleep(0.03)
    state.circuits_open[provider] = open_
    action = "opened" if open_ else "closed"
    _log("provider_circuit", f"Circuit {action} for {provider}", provider=provider)
    return ToolResult(
        name="open_provider_circuit",
        ok=True,
        summary=f"Circuit breaker {action} for provider {provider}",
        data={"provider": provider, "open": open_},
    )


async def set_dns_override(hostname: str, target: str) -> ToolResult:
    await asyncio.sleep(0.05)
    state.dns_overrides[hostname] = target
    _log("dns_override", f"{hostname} → {target}")
    return ToolResult(
        name="set_dns_override",
        ok=True,
        summary=f"Pinned {hostname} to {target}",
        data={"overrides": dict(state.dns_overrides)},
    )


async def simulate_fault(pop: str, loss_pct: float = 25.0, latency_ms: float = 120.0) -> ToolResult:
    await asyncio.sleep(0.05)
    key = pop.upper()
    if key not in state.peers:
        return ToolResult(name="simulate_fault", ok=False, summary=f"Unknown PoP {pop}")
    state.peers[key].update({"status": "degraded", "loss_pct": loss_pct, "latency_ms": latency_ms})
    _log("simulate_fault", f"Injected fault at {key}", pop=key)
    return ToolResult(
        name="simulate_fault",
        ok=True,
        summary=f"Injected degradation at {key}: loss={loss_pct}% latency={latency_ms}ms",
        data={"peer": state.peers[key]},
    )


async def heal_pop(pop: str) -> ToolResult:
    await asyncio.sleep(0.05)
    key = pop.upper()
    if key not in state.peers:
        return ToolResult(name="heal_pop", ok=False, summary=f"Unknown PoP {pop}")
    state.peers[key] = {"status": "up", "loss_pct": 0.2, "latency_ms": 20}
    _log("heal_pop", f"Restored {key}")
    return ToolResult(name="heal_pop", ok=True, summary=f"Restored {key} to healthy", data=state.peers[key])


TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "get_network_status": get_network_status,
    "dampen_bgp_peer": dampen_bgp_peer,
    "shift_anycast_weight": shift_anycast_weight,
    "open_provider_circuit": open_provider_circuit,
    "set_dns_override": set_dns_override,
    "simulate_fault": simulate_fault,
    "heal_pop": heal_pop,
}


TOOL_SPECS = [
    {
        "name": "get_network_status",
        "description": "Get PoP health, loss, latency, and anycast weights. Optional region filter.",
        "parameters": {"region": {"type": "string", "optional": True}},
    },
    {
        "name": "dampen_bgp_peer",
        "description": "Temporarily dampen a flapping BGP peer.",
        "parameters": {"peer": {"type": "string"}, "minutes": {"type": "integer", "default": 15}},
    },
    {
        "name": "shift_anycast_weight",
        "description": "Move anycast traffic weight from one PoP to another.",
        "parameters": {
            "from_pop": {"type": "string"},
            "to_pop": {"type": "string"},
            "amount": {"type": "integer", "default": 25},
        },
    },
    {
        "name": "open_provider_circuit",
        "description": "Open or close a circuit breaker for an STT/TTS/LLM provider.",
        "parameters": {"provider": {"type": "string"}, "open_": {"type": "boolean", "default": True}},
    },
    {
        "name": "set_dns_override",
        "description": "Pin a hostname to an alternate target during DNS incidents.",
        "parameters": {"hostname": {"type": "string"}, "target": {"type": "string"}},
    },
    {
        "name": "simulate_fault",
        "description": "Inject a synthetic fault at a PoP for drills.",
        "parameters": {
            "pop": {"type": "string"},
            "loss_pct": {"type": "number", "default": 25},
            "latency_ms": {"type": "number", "default": 120},
        },
    },
    {
        "name": "heal_pop",
        "description": "Restore a PoP to healthy baseline.",
        "parameters": {"pop": {"type": "string"}},
    },
]


async def invoke_tool(name: str, **kwargs: Any) -> ToolResult:
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        return ToolResult(name=name, ok=False, summary=f"Unknown tool {name}")
    return await fn(**kwargs)


# In-flight MCP tool tasks keyed by (call_id, pass_id) for graceful abort.
_inflight_tools: dict[tuple[int, str], set[asyncio.Task]] = {}


async def invoke_tool_tracked(
    call_id: int | None,
    name: str,
    *,
    pass_id: str | None = None,
    **kwargs: Any,
) -> ToolResult:
    """Run an MCP tool as a cancellable task bound to a call/pass."""
    if call_id is None:
        return await invoke_tool(name, **kwargs)

    async def _run() -> ToolResult:
        return await invoke_tool(name, **kwargs)

    task = asyncio.create_task(_run())
    key = (call_id, pass_id or "")
    bucket = _inflight_tools.setdefault(key, set())
    bucket.add(task)

    def _done(t: asyncio.Task) -> None:
        bucket.discard(t)
        if not bucket:
            _inflight_tools.pop(key, None)

    task.add_done_callback(_done)
    try:
        return await task
    except asyncio.CancelledError:
        return ToolResult(
            name=name,
            ok=False,
            summary=f"Tool {name} aborted before completion",
            data={"aborted": True},
        )


async def cleanup_pass_tools(
    call_id: int,
    *,
    pass_id: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Cancel and join in-flight MCP tool tasks for a call (optionally one pass)."""
    if pass_id is not None:
        keys = [(call_id, pass_id)]
    else:
        keys = [k for k in list(_inflight_tools) if k[0] == call_id]

    tasks: list[asyncio.Task] = []
    for key in keys:
        tasks.extend(list(_inflight_tools.get(key, set())))

    for t in tasks:
        if not t.done():
            t.cancel()
    closed = 0
    timed_out = False
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        closed = len(done)
        if pending:
            timed_out = True
            for t in pending:
                t.cancel()
            await asyncio.wait(pending, timeout=1.0)
    for key in keys:
        _inflight_tools.pop(key, None)
    _log(
        "cleanup_pass_tools",
        f"Closed MCP tools for call {call_id} pass={pass_id}",
        call_id=call_id,
        closed=closed,
    )
    return {
        "call_id": call_id,
        "pass_id": pass_id,
        "tools_closed": closed,
        "tools_total": len(tasks),
        "timed_out": timed_out,
    }


def tools_prompt_block() -> str:
    lines = ["Available MCP network mitigation tools:"]
    for t in TOOL_SPECS:
        lines.append(f"- {t['name']}: {t['description']}")
    lines.append(
        "When mitigation is needed, respond with a JSON action block on its own line:\n"
        'ACTION: {"tool":"tool_name","args":{...}}\n'
        "You may emit multiple ACTION lines. Then explain the plan briefly to the operator.\n"
        "If the operator changes approach mid-pass, stop the current tool plan and follow the new direction."
    )
    return "\n".join(lines)
