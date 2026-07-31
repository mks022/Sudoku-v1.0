# BGP flap — edge PoP AMS-1 (2024-11-12)
Symptoms: intermittent packet loss 8–35% on customer VPN tunnels terminating at AMS-1.
Root cause: upstream peer reset due to malformed UPDATE; BFD timers too aggressive.
Mitigation applied:
1. Dampened flapping peer for 15 minutes
2. Shifted traffic to AMS-2 via anycast weight change
3. Raised BFD multiplier from 3 to 5
Resolution time: 22 minutes. Customers recovered after weight change propagated (~90s).
Performance: p95 latency AMS-1 rose from 18ms to 140ms during event; returned to baseline after shift.
