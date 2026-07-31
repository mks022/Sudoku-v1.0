# NetGuard — Network Fault Voice Agent

Voice + chat operator console built on a **Pipecat-style** pipeline with **STT**, **TTS**, **LLM**, **Silero VAD**, and **MCP** mitigation tools. The stack retries, circuit-breaks, and fails over providers to ride out network and API faults. The LLM prompt is grounded in **past outages + performance** (files) and **manual RAG** notes.

## Features

- Hybrid **voice + keyboard** channel (browser mic / speech recognition + chat)
- Live **transaction stream** (VAD → STT → RAG → LLM → MCP → TTS)
- **Call records** with transcripts and fault-event counts
- Frontend panel for **provider API secrets** (LLM / STT / TTS / VAD / MCP) with primary + fallback
- Built-in MCP tools: PoP status, BGP dampen, anycast shift, DNS override, provider circuit, fault drill
- Demo LLM mode when no API key is configured

## Architecture

```
frontend (Vite/React)
  ├─ Console: chat + mic, live TX, network plane
  ├─ Calls: session history
  ├─ Providers: API secrets & models
  └─ RAG: manual outage/runbook notes
        │  HTTP + WebSocket
backend (FastAPI)
  ├─ /api/chat          resilient LLM + RAG + MCP
  ├─ /api/ws/voice      voice session (VAD/STT/TTS hooks)
  ├─ /api/ws/transactions  live event bus
  ├─ resilience         retry · circuit breaker · fallback
  └─ data/outages/*.md  seeded historical incidents
```

## Quick start

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

API: http://127.0.0.1:8000/docs

### Frontend

```bash
cd frontend
npm install
npm run dev
```

UI: http://127.0.0.1:5173

1. Open **Providers** and paste API keys (OpenAI / Deepgram / Cartesia, etc.)
2. Open **Console**, type `status` or `BGP flap at AMS-1`, or use **Mic**
3. Watch **Live transactions** and **Network plane** update
4. Review **Calls** for history

## Pipeline stages

| Stage | Role |
|-------|------|
| VAD (Silero) | Speech segment / end-of-utterance |
| STT | Deepgram primary, OpenAI Whisper fallback |
| RAG | TF-IDF over outage files + manual docs |
| LLM | OpenAI-compatible chat; demo brain without keys |
| MCP | In-process network mitigation tools |
| TTS | Cartesia / OpenAI; browser speechSynthesis fallback |
| Resilience | Tenacity retries, circuit breaker, provider failover, text degrade |

## Environment

Optional `.env` in `backend/`:

```
HOST=0.0.0.0
PORT=8000
```

Provider secrets are stored via the frontend → `/api/providers` (SQLite). Keys are masked on read.

## Sample prompts

- `What's the network status?`
- `BGP flap on AMS-1 — mitigate like last time`
- `DNS SERVFAIL in region — pin critical hosts`
- `STT returning 503 — open the circuit`
- `Simulate fault at DFW-E` / `Heal AMS-1`
