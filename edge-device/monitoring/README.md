# Monitoring Subsystem

## Purpose
The monitoring module ensures the edge computer operates safely within strict hardware limits and maintains a secure, verifiable record of system events.

## Implemented Requirements
- **REQ-HW-02 (Memory Bounds):** Tracks resident set size (RSS) memory consumption via Linux pseudo-filesystems (`/proc/self/status`) to ensure allocated RAM stays strictly below **2 GB**.
- **REQ-AUD-01 (Audit Trail):** Implements an append-only, SHA-256 hash-linked log chain that records anomalies, state transitions, and system events to guarantee tamper detection.