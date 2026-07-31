# QUIC / mTLS handshake failures after cert rotation (2026-01-20)
Symptoms: new sessions fail; existing TCP long-polls still work.
Root cause: intermediate CA not deployed to edge fleet.
Mitigation:
1. Rollback cert bundle on 30% canary
2. Force TLS1.2 fallback path for voice WebSockets
3. Re-deploy intermediate to remaining nodes
Lesson: Voice WebSocket stacks must support TLS fallback and session resume.
