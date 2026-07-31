# External API degradation — STT/LLM providers (2025-09-01)
Symptoms: voice agent latency spikes, partial transcripts, 429/503 from primary STT.
Root cause: provider regional incident.
Mitigation playbook:
1. Circuit-break primary STT after 3 consecutive failures
2. Fail over to secondary STT (Deepgram ↔ OpenAI Whisper)
3. Degrade gracefully to text chat if both voice paths fail
4. Buffer TTS audio and resume on reconnect
Operator note: Keep fallback API keys configured in the frontend providers panel.
