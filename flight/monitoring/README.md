# Flight Monitoring

This module is for onboard health and resource monitoring, plus flight-side event/audit support. Keep it independent of ground dashboards and services. Component tests are in `tests/`.

Implemented: `resource_monitor.py` (temperature, agent RAM, PMIC power, CPU against PR#1 limits).
The hash-chained audit writer is shared code in `shared/audit/`. See
[docs/AUDIT_AND_MONITORING.md](../../docs/AUDIT_AND_MONITORING.md).
