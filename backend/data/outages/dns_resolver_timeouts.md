# DNS resolver timeouts — regional anycast (2025-02-03)
Symptoms: elevated SERVFAIL and client-reported "no internet" despite healthy HTTP probes.
Root cause: recursive resolver cache poisoned by stale glue after zone cut change.
Mitigation:
1. Flush resolver caches in affected region
2. Pin authoritative NS via static override for critical zones
3. Fail open to secondary resolver pool
Notes: Always check DNS before blaming L3. Voice STT/TTS providers failed when resolvers timed out.
