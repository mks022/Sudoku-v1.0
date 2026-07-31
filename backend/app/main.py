from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings
from app.models.db import SessionLocal, init_db, RagDocument
from app.rag.index import load_rag_from_db, seed_outage_files
from app.services.providers import ensure_default_providers
from sqlalchemy import select


async def seed_manual_rag() -> None:
    async with SessionLocal() as session:
        existing = (await session.execute(select(RagDocument))).scalars().first()
        if existing:
            return
        manuals = [
            RagDocument(
                title="Operator runbook — voice path failover",
                content=(
                    "If STT returns empty transcripts or 503s: open circuit on primary STT, "
                    "switch to fallback Whisper, and keep the chat keyboard enabled so operators "
                    "can continue mitigation without voice. Log fault_events on the call record."
                ),
                tags=["runbook", "stt", "failover"],
                source="manual",
            ),
            RagDocument(
                title="Performance baseline — PoP SLOs",
                content=(
                    "Healthy PoP baselines: loss < 0.5%, latency p95 < 40ms metro / < 90ms intercontinental. "
                    "Anycast weight shifts take ~60–120s to fully converge. BGP dampen default 15 minutes."
                ),
                tags=["slo", "performance"],
                source="manual",
            ),
            RagDocument(
                title="MCP mitigation authority",
                content=(
                    "NetGuard may dampen peers, shift anycast, set DNS overrides, and open provider circuits. "
                    "For fiber cuts, prefer automatic FRR then manual weight shift. Always announce actions "
                    "to the operator in plain language after ACTION lines."
                ),
                tags=["mcp", "authority"],
                source="manual",
            ),
        ]
        session.add_all(manuals)
        await session.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed_outage_files()
    await init_db()
    async with SessionLocal() as session:
        await ensure_default_providers(session)
    await seed_manual_rag()
    async with SessionLocal() as session:
        await load_rag_from_db(session)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins + ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "docs": "/docs",
        "api": "/api/health",
    }
