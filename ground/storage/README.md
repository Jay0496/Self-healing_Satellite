# Ground Storage

This module is for ground-side persistence and query access for received telemetry, incidents, and audit records. It is distinct from onboard buffering in `flight/storage/` and should not assume a specific cloud provider.

Implemented: `audit_store.py` re-verifies the downlinked flight audit chain;
`audit_cli.py` verifies, searches and exports it. See
[docs/AUDIT_AND_MONITORING.md](../../docs/AUDIT_AND_MONITORING.md).
