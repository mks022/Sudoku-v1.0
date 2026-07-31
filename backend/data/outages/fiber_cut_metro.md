# Metro fiber cut — DFW ring (2025-06-18)
Symptoms: hard down for 12% of enterprise circuits on ring east.
Root cause: backhoe cut on shared conduit.
Mitigation:
1. Automatic MPLS FRR switched to west ring (<50ms for protected LSPs)
2. Non-protected customers manually rerouted via LTE backup
3. Opened bridge with field ops; ETA 4h
Performance impact: protected customers saw <1% packet loss; unprotected 100% until LTE failover (~3 min).
