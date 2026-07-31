from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db import RagDocument


@dataclass
class RagChunk:
    title: str
    content: str
    score: float
    source: str
    tags: list[str]


class RagIndex:
    """Lightweight TF-IDF RAG over past outages + manual operator notes."""

    def __init__(self) -> None:
        self._docs: list[dict] = []
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None

    def _tokenize(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.lower()).strip()

    def rebuild(self, documents: list[dict]) -> None:
        self._docs = documents
        if not documents:
            self._vectorizer = None
            self._matrix = None
            return
        corpus = [self._tokenize(f"{d['title']}\n{d['content']}") for d in documents]
        self._vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=4096)
        self._matrix = self._vectorizer.fit_transform(corpus)

    def query(self, text: str, top_k: int | None = None) -> list[RagChunk]:
        if not self._docs or self._vectorizer is None or self._matrix is None:
            return []
        k = top_k or settings.max_rag_chunks
        q = self._vectorizer.transform([self._tokenize(text)])
        scores = cosine_similarity(q, self._matrix)[0]
        idxs = np.argsort(scores)[::-1][:k]
        chunks: list[RagChunk] = []
        for i in idxs:
            if scores[i] <= 0.01:
                continue
            d = self._docs[i]
            chunks.append(
                RagChunk(
                    title=d["title"],
                    content=d["content"],
                    score=float(scores[i]),
                    source=d.get("source", "manual"),
                    tags=d.get("tags", []),
                )
            )
        return chunks

    def format_prompt_block(self, chunks: list[RagChunk]) -> str:
        if not chunks:
            return "No matching historical outages or runbooks were found."
        parts = ["Historical outages & operator notes (RAG):"]
        for i, c in enumerate(chunks, 1):
            parts.append(
                f"[{i}] {c.title} (score={c.score:.2f}, source={c.source}, tags={','.join(c.tags)})\n{c.content}"
            )
        return "\n\n".join(parts)


rag_index = RagIndex()


async def load_rag_from_db(session: AsyncSession) -> None:
    result = await session.execute(select(RagDocument).order_by(RagDocument.id))
    rows = result.scalars().all()
    docs = [
        {
            "id": r.id,
            "title": r.title,
            "content": r.content,
            "tags": r.tags or [],
            "source": r.source,
        }
        for r in rows
    ]
    # Also ingest markdown files from data/outages
    for path in Path(settings.outages_dir).glob("*.md"):
        docs.append(
            {
                "id": f"file:{path.name}",
                "title": path.stem.replace("_", " ").title(),
                "content": path.read_text(encoding="utf-8"),
                "tags": ["outage", "file"],
                "source": "file",
            }
        )
    rag_index.rebuild(docs)


def seed_outage_files() -> None:
    samples = {
        "bgp_flap_edge_2024.md": """# BGP flap — edge PoP AMS-1 (2024-11-12)
Symptoms: intermittent packet loss 8–35% on customer VPN tunnels terminating at AMS-1.
Root cause: upstream peer reset due to malformed UPDATE; BFD timers too aggressive.
Mitigation applied:
1. Dampened flapping peer for 15 minutes
2. Shifted traffic to AMS-2 via anycast weight change
3. Raised BFD multiplier from 3 to 5
Resolution time: 22 minutes. Customers recovered after weight change propagated (~90s).
Performance: p95 latency AMS-1 rose from 18ms to 140ms during event; returned to baseline after shift.
""",
        "dns_resolver_timeouts.md": """# DNS resolver timeouts — regional anycast (2025-02-03)
Symptoms: elevated SERVFAIL and client-reported "no internet" despite healthy HTTP probes.
Root cause: recursive resolver cache poisoned by stale glue after zone cut change.
Mitigation:
1. Flush resolver caches in affected region
2. Pin authoritative NS via static override for critical zones
3. Fail open to secondary resolver pool
Notes: Always check DNS before blaming L3. Voice STT/TTS providers failed when resolvers timed out.
""",
        "fiber_cut_metro.md": """# Metro fiber cut — DFW ring (2025-06-18)
Symptoms: hard down for 12% of enterprise circuits on ring east.
Root cause: backhoe cut on shared conduit.
Mitigation:
1. Automatic MPLS FRR switched to west ring (<50ms for protected LSPs)
2. Non-protected customers manually rerouted via LTE backup
3. Opened bridge with field ops; ETA 4h
Performance impact: protected customers saw <1% packet loss; unprotected 100% until LTE failover (~3 min).
""",
        "provider_api_degradation.md": """# External API degradation — STT/LLM providers (2025-09-01)
Symptoms: voice agent latency spikes, partial transcripts, 429/503 from primary STT.
Root cause: provider regional incident.
Mitigation playbook:
1. Circuit-break primary STT after 3 consecutive failures
2. Fail over to secondary STT (Deepgram ↔ OpenAI Whisper)
3. Degrade gracefully to text chat if both voice paths fail
4. Buffer TTS audio and resume on reconnect
Operator note: Keep fallback API keys configured in the frontend providers panel.
""",
        "quic_mtls_handshake.md": """# QUIC / mTLS handshake failures after cert rotation (2026-01-20)
Symptoms: new sessions fail; existing TCP long-polls still work.
Root cause: intermediate CA not deployed to edge fleet.
Mitigation:
1. Rollback cert bundle on 30% canary
2. Force TLS1.2 fallback path for voice WebSockets
3. Re-deploy intermediate to remaining nodes
Lesson: Voice WebSocket stacks must support TLS fallback and session resume.
""",
    }
    for name, body in samples.items():
        path = settings.outages_dir / name
        if not path.exists():
            path.write_text(body, encoding="utf-8")
