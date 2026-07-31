from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import ProviderConfig
from app.models.schemas import ProviderConfigIn, ProviderKind


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"


DEFAULT_PROVIDERS: list[dict[str, Any]] = [
    {
        "kind": "llm",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "enabled": True,
        "priority": 0,
        "is_fallback": False,
    },
    {
        "kind": "llm",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "enabled": False,
        "priority": 1,
        "is_fallback": True,
        "extra": {"label": "fallback-llm"},
    },
    {
        "kind": "stt",
        "provider": "deepgram",
        "model": "nova-2",
        "base_url": "https://api.deepgram.com",
        "enabled": True,
        "priority": 0,
        "is_fallback": False,
    },
    {
        "kind": "stt",
        "provider": "openai",
        "model": "whisper-1",
        "base_url": "https://api.openai.com/v1",
        "enabled": True,
        "priority": 1,
        "is_fallback": True,
    },
    {
        "kind": "tts",
        "provider": "cartesia",
        "model": "sonic-english",
        "base_url": "https://api.cartesia.ai",
        "enabled": True,
        "priority": 0,
        "is_fallback": False,
    },
    {
        "kind": "tts",
        "provider": "openai",
        "model": "gpt-4o-mini-tts",
        "base_url": "https://api.openai.com/v1",
        "enabled": True,
        "priority": 1,
        "is_fallback": True,
    },
    {
        "kind": "vad",
        "provider": "silero",
        "model": "silero_vad",
        "enabled": True,
        "priority": 0,
        "is_fallback": False,
    },
    {
        "kind": "mcp",
        "provider": "builtin-network",
        "model": "",
        "base_url": "",
        "enabled": True,
        "priority": 0,
        "is_fallback": False,
        "extra": {"transport": "in-process"},
    },
]


async def ensure_default_providers(session: AsyncSession) -> None:
    existing = (await session.execute(select(ProviderConfig))).scalars().first()
    if existing:
        return
    for item in DEFAULT_PROVIDERS:
        session.add(ProviderConfig(**item, api_key=""))
    await session.commit()


async def list_providers(session: AsyncSession, kind: Optional[ProviderKind] = None) -> list[ProviderConfig]:
    stmt = select(ProviderConfig).order_by(ProviderConfig.kind, ProviderConfig.priority)
    if kind:
        stmt = stmt.where(ProviderConfig.kind == kind.value)
    return list((await session.execute(stmt)).scalars().all())


async def upsert_provider(session: AsyncSession, payload: ProviderConfigIn, provider_id: int | None = None) -> ProviderConfig:
    if provider_id:
        row = await session.get(ProviderConfig, provider_id)
        if not row:
            raise ValueError("Provider not found")
    else:
        row = ProviderConfig()
        session.add(row)

    row.kind = payload.kind.value
    row.provider = payload.provider
    if payload.api_key or not provider_id:
        row.api_key = payload.api_key
    row.base_url = payload.base_url
    row.model = payload.model
    row.extra = payload.extra
    row.enabled = payload.enabled
    row.is_fallback = payload.is_fallback
    row.priority = payload.priority
    await session.commit()
    await session.refresh(row)
    return row


async def delete_provider(session: AsyncSession, provider_id: int) -> None:
    row = await session.get(ProviderConfig, provider_id)
    if row:
        await session.delete(row)
        await session.commit()


async def providers_by_kind(session: AsyncSession, kind: str) -> list[ProviderConfig]:
    stmt = (
        select(ProviderConfig)
        .where(ProviderConfig.kind == kind, ProviderConfig.enabled.is_(True))
        .order_by(ProviderConfig.is_fallback, ProviderConfig.priority)
    )
    return list((await session.execute(stmt)).scalars().all())
