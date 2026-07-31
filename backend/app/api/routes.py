from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from app.models.db import SessionLocal, RagDocument
from app.models.schemas import (
    CallCreate,
    CallOut,
    CallStatus,
    ChatReply,
    MessageIn,
    MessageOut,
    MessageRole,
    ProviderConfigIn,
    ProviderConfigOut,
    ProviderTemplateApply,
    RagDocumentIn,
    RagDocumentOut,
    SessionStart,
    TransactionEvent,
    TransactionKind,
)
from app.pipeline.voice import handle_voice_ws, pipeline_info
from app.rag.index import load_rag_from_db, rag_index
from app.services.agent import agent_service
from app.services.adapters import TEMPLATES, catalog, resolve_protocol
from app.services.events import event_bus
from app.services.providers import (
    create_from_template,
    delete_provider,
    list_providers,
    mask_key,
    upsert_provider,
)
from app.mcp_tools.network import TOOL_SPECS, state as network_state

router = APIRouter()


async def get_session():
    async with SessionLocal() as session:
        yield session


def call_to_out(call) -> CallOut:
    preview = ""
    count = 0
    # Avoid lazy-loading relationships in async context (MissingGreenlet).
    state = attributes.instance_state(call)
    messages = state.dict.get("messages")
    if messages is not None:
        count = len(messages)
        for m in reversed(messages):
            if m.role == "user":
                preview = m.content[:160]
                break
    return CallOut(
        id=call.id,
        channel=call.channel,
        title=call.title,
        status=CallStatus(call.status),
        started_at=call.started_at,
        ended_at=call.ended_at,
        transcript_preview=preview,
        message_count=count,
        fault_events=call.fault_events or 0,
        metadata=call.metadata_json or {},
    )


def provider_to_out(row) -> ProviderConfigOut:
    return ProviderConfigOut(
        id=row.id,
        kind=row.kind,
        provider=row.provider,
        api_key="",
        api_key_masked=mask_key(row.api_key or ""),
        base_url=row.base_url or "",
        model=row.model or "",
        extra=row.extra or {},
        enabled=row.enabled,
        is_fallback=row.is_fallback,
        priority=row.priority,
        updated_at=row.updated_at or datetime.utcnow(),
        protocol=resolve_protocol(row),
    )


@router.get("/health")
async def health():
    return {
        "ok": True,
        "pipeline": pipeline_info(),
        "circuits": agent_service.executor.status(),
        "rag_docs": len(rag_index._docs),
    }


@router.get("/pipeline")
async def get_pipeline():
    return pipeline_info()


@router.get("/providers/catalog")
async def providers_catalog():
    return catalog()


@router.get("/providers", response_model=list[ProviderConfigOut])
async def get_providers(kind: str | None = None, session: AsyncSession = Depends(get_session)):
    rows = await list_providers(session, kind)
    return [provider_to_out(r) for r in rows]


@router.post("/providers", response_model=ProviderConfigOut)
async def create_provider(payload: ProviderConfigIn, session: AsyncSession = Depends(get_session)):
    row = await upsert_provider(session, payload)
    return provider_to_out(row)


@router.post("/providers/from-template", response_model=ProviderConfigOut)
async def provider_from_template(payload: ProviderTemplateApply, session: AsyncSession = Depends(get_session)):
    template = next((t for t in TEMPLATES if t["id"] == payload.template_id), None)
    if not template:
        raise HTTPException(404, f"Unknown template `{payload.template_id}`")
    overrides: dict = {}
    if payload.provider:
        overrides["provider"] = payload.provider
    if payload.base_url:
        overrides["base_url"] = payload.base_url
    if payload.model:
        overrides["model"] = payload.model
    if payload.extra:
        overrides["extra"] = {**(template.get("extra") or {}), **payload.extra}
    row = await create_from_template(
        session,
        template,
        api_key=payload.api_key,
        enabled=payload.enabled,
        is_fallback=payload.is_fallback,
        priority=payload.priority,
        overrides=overrides,
    )
    return provider_to_out(row)


@router.put("/providers/{provider_id}", response_model=ProviderConfigOut)
async def update_provider(
    provider_id: int, payload: ProviderConfigIn, session: AsyncSession = Depends(get_session)
):
    try:
        row = await upsert_provider(session, payload, provider_id=provider_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return provider_to_out(row)


@router.delete("/providers/{provider_id}")
async def remove_provider(provider_id: int, session: AsyncSession = Depends(get_session)):
    await delete_provider(session, provider_id)
    return {"ok": True}


@router.get("/calls", response_model=list[CallOut])
async def get_calls(session: AsyncSession = Depends(get_session)):
    calls = await agent_service.list_calls(session)
    return [call_to_out(c) for c in calls]


@router.post("/calls", response_model=CallOut)
async def start_call(payload: CallCreate, session: AsyncSession = Depends(get_session)):
    call = await agent_service.create_call(session, channel=payload.channel, title=payload.title)
    return call_to_out(call)


@router.get("/calls/{call_id}", response_model=CallOut)
async def get_call(call_id: int, session: AsyncSession = Depends(get_session)):
    call = await agent_service.get_call(session, call_id)
    if not call:
        raise HTTPException(404, "Call not found")
    return call_to_out(call)


@router.get("/calls/{call_id}/messages", response_model=list[MessageOut])
async def get_messages(call_id: int, session: AsyncSession = Depends(get_session)):
    call = await agent_service.get_call(session, call_id)
    if not call:
        raise HTTPException(404, "Call not found")
    return [
        MessageOut(
            id=m.id,
            call_id=m.call_id,
            role=MessageRole(m.role),
            content=m.content,
            created_at=m.created_at,
            meta=m.meta or {},
        )
        for m in call.messages
    ]


@router.post("/calls/{call_id}/end", response_model=CallOut)
async def end_call(call_id: int, session: AsyncSession = Depends(get_session)):
    try:
        await agent_service.end_call(session, call_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    call = await agent_service.get_call(session, call_id)
    return call_to_out(call)


@router.post("/chat", response_model=ChatReply)
async def chat(payload: MessageIn, session: AsyncSession = Depends(get_session)):
    try:
        result = await agent_service.handle_chat(
            session,
            content=payload.content,
            call_id=payload.call_id,
            channel=payload.channel,
            abort_current=payload.abort_current,
            supersede_reason=payload.supersede_reason,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    u, a = result["user_message"], result["assistant_message"]
    return ChatReply(
        call_id=result["call"].id,
        user_message=MessageOut(
            id=u.id,
            call_id=u.call_id,
            role=MessageRole(u.role),
            content=u.content,
            created_at=u.created_at,
            meta=u.meta or {},
        ),
        assistant_message=MessageOut(
            id=a.id,
            call_id=a.call_id,
            role=MessageRole(a.role),
            content=a.content,
            created_at=a.created_at,
            meta=a.meta or {},
        ),
        rag_context=result["rag_context"],
        tools_used=result["tools_used"],
        narrations=result.get("narrations") or [],
        aborted=bool(result.get("aborted")),
        abort_info=result.get("abort_info"),
        pass_id=str(result.get("pass_id") or ""),
        transactions=event_bus.history()[-20:],
    )


@router.post("/calls/{call_id}/abort")
async def abort_pass(call_id: int, reason: str = "operator_abort"):
    from app.mcp_tools.network import cleanup_pass_tools
    from app.services.pass_manager import pass_manager

    info = await pass_manager.abort(call_id, reason=reason, wait=True)
    cleanup = await cleanup_pass_tools(call_id)
    info["mcp_cleanup"] = cleanup
    await event_bus.emit(
        TransactionKind.system,
        "Operator abort",
        status="info" if info.get("aborted") else "info",
        detail=reason,
        call_id=call_id,
        meta=info,
    )
    return info


@router.get("/calls/{call_id}/pass")
async def pass_status(call_id: int):
    from app.services.pass_manager import pass_manager

    return pass_manager.status(call_id)


@router.get("/transactions", response_model=list[TransactionEvent])
async def recent_transactions():
    return event_bus.history()


@router.get("/mcp/tools")
async def mcp_tools():
    return {
        "tools": TOOL_SPECS,
        "network_state": {
            "peers": network_state.peers,
            "anycast_weights": network_state.anycast_weights,
            "circuits_open": network_state.circuits_open,
            "dns_overrides": network_state.dns_overrides,
            "incident_log": network_state.incident_log[-20:],
        },
    }


@router.get("/rag/documents", response_model=list[RagDocumentOut])
async def list_rag(session: AsyncSession = Depends(get_session)):
    from sqlalchemy import select

    rows = (await session.execute(select(RagDocument).order_by(RagDocument.id.desc()))).scalars().all()
    return [
        RagDocumentOut(
            id=r.id,
            title=r.title,
            content=r.content,
            tags=r.tags or [],
            source=r.source,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/rag/documents", response_model=RagDocumentOut)
async def add_rag(payload: RagDocumentIn, session: AsyncSession = Depends(get_session)):
    row = RagDocument(
        title=payload.title,
        content=payload.content,
        tags=payload.tags,
        source=payload.source,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await load_rag_from_db(session)
    return RagDocumentOut(
        id=row.id,
        title=row.title,
        content=row.content,
        tags=row.tags or [],
        source=row.source,
        created_at=row.created_at,
    )


@router.post("/sessions", response_model=CallOut)
async def create_session(payload: SessionStart, session: AsyncSession = Depends(get_session)):
    call = await agent_service.create_call(session, channel=payload.channel, title=payload.title)
    return call_to_out(call)


@router.websocket("/ws/transactions")
async def ws_transactions(websocket: WebSocket):
    await websocket.accept()
    q = await event_bus.subscribe()
    try:
        await websocket.send_json(
            {"type": "snapshot", "events": [e.model_dump(mode="json") for e in event_bus.history()]}
        )
        while True:
            try:
                event = await q.get()
                await websocket.send_json({"type": "event", "event": event.model_dump(mode="json")})
            except WebSocketDisconnect:
                break
    finally:
        await event_bus.unsubscribe(q)


@router.websocket("/ws/voice/{call_id}")
async def ws_voice(websocket: WebSocket, call_id: int):
    await handle_voice_ws(websocket, SessionLocal, call_id)
