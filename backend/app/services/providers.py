from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import ProviderConfig
from app.models.schemas import ProviderConfigIn


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"


async def ensure_default_providers(session: AsyncSession) -> None:
    """Intentionally a no-op — providers are fully user-configured.

    Kept as a lifecycle hook so older call sites keep working.
    """
    _ = session
    return


async def list_providers(session: AsyncSession, kind: Optional[str] = None) -> list[ProviderConfig]:
    stmt = select(ProviderConfig).order_by(ProviderConfig.kind, ProviderConfig.priority, ProviderConfig.id)
    if kind:
        stmt = stmt.where(ProviderConfig.kind == kind)
    return list((await session.execute(stmt)).scalars().all())


async def upsert_provider(
    session: AsyncSession,
    payload: ProviderConfigIn,
    provider_id: int | None = None,
) -> ProviderConfig:
    if provider_id:
        row = await session.get(ProviderConfig, provider_id)
        if not row:
            raise ValueError("Provider not found")
    else:
        row = ProviderConfig()
        session.add(row)

    row.kind = payload.kind.strip().lower()
    row.provider = payload.provider.strip()
    if payload.clear_api_key:
        row.api_key = ""
    elif payload.api_key or not provider_id:
        row.api_key = payload.api_key
    row.base_url = payload.base_url
    row.model = payload.model
    row.extra = payload.extra or {}
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
        .order_by(ProviderConfig.is_fallback, ProviderConfig.priority, ProviderConfig.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def create_from_template(
    session: AsyncSession,
    template: dict[str, Any],
    *,
    api_key: str = "",
    is_fallback: bool = False,
    priority: int = 0,
    enabled: bool = True,
    overrides: dict[str, Any] | None = None,
) -> ProviderConfig:
    overrides = overrides or {}
    payload = ProviderConfigIn(
        kind=str(overrides.get("kind") or template["kind"]),
        provider=str(overrides.get("provider") or template["provider"]),
        api_key=api_key,
        base_url=str(overrides.get("base_url") if "base_url" in overrides else template.get("base_url") or ""),
        model=str(overrides.get("model") if "model" in overrides else template.get("model") or ""),
        extra=dict(overrides.get("extra") or template.get("extra") or {}),
        enabled=enabled,
        is_fallback=is_fallback,
        priority=priority,
    )
    return await upsert_provider(session, payload)
