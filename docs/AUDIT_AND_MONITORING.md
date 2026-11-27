# Audit trail and edge-resource monitoring

This document explains how the onboard agent records a tamper-evident history of what it
saw and did, how that history reaches the ground through the blackout store-and-forward
path, how operators verify/search/export it, and how the edge computer's resources are
monitored. It covers **REQ-AUD-01** and the monitoring parts of **REQ-HW-01/02/03**,
**REQ-ML-01** and **REQ-FSM-01**, and replaces the standalone C++ prototypes from
`feature/edge-device-monitoring` with Python that runs inside the real demo pipeline.
Link encryption is described separately in [SECURITY.md](SECURITY.md).

## 1. End-to-end flow

```
 Pi (flight)                                                         Mac (ground)
 ┌──────────────────────────────── flight SQLite file ────────┐
 │ telemetry table   events table   audit_log (hash chain)     │
 └──────▲───────────────▲───────────────▲───────────▲─────────┘
        │               │               │           │
   collector        collector       collector    AI detector
  (samples)      (link states)   LINK_STATE, COMMAND, ANOMALY_DETECTED / CLEARED,
                                 SECURITY_REJECT,     AI_PERFORMANCE, AI_START
                                 KEY_ROTATION, RESOURCE_SAMPLE/LIMIT_*, AGENT_START/STOP
        │
        └── when link is ONLINE/RECOVERING, oldest first, 100 per batch, AES-256-GCM sealed:
               1. downlink/events   2. downlink/audit   3. downlink/telemetry
                                         │
                                         ▼
                        mqtt_ground → GroundAuditStore (flight_audit table)
                        recomputes every hash + prev link → VERIFIED / flagged
                        ACK (sealed) → Pi marks entries delivered
 Ground operator actions (commands sent/results, ground-side security rejections,
 integrity alerts) → separate ground hash chain: ground/storage/operator_audit.sqlite3
```

During a **BLACKOUT** nothing is sent; audit entries keep being appended locally (for
example the AI's `ANOMALY_DETECTED` while the link is down). On **RECOVERING** the backlog
is delivered after link events and before routine telemetry (REQ-COM-02 "critical first"),
and the Pi stays in RECOVERING until telemetry, events **and** audit entries are all ACKed.
The legacy HTTP path does the same through `POST /ingest-audit`.

## 2. The hash chain (`shared/audit/chain.py`)

Each entry: `chain_id, seq, entry_uid, recorded_at (UTC, ms), event_type, actor, details
(canonical JSON), prev_hash, entry_hash`, where

```
entry_hash = SHA-256(canonical JSON of every field above except entry_hash)
prev_hash  = entry_hash of seq-1   (64 zeros for seq 1)
```

* **Append-only:** SQLite triggers abort any UPDATE/DELETE of audited columns. Only the
  delivery column `acknowledged_at` (outside the hash) can change.
* **Tamper-evident:** if someone bypasses the triggers (raw file edit), verification
  reports the first broken entry: a modified entry fails its own hash, a deleted or
  inserted one breaks the sequence/link, and a rewritten entry with a recomputed hash
  breaks the *next* link.
* **One chain, several writers:** the collector and the AI detector are separate processes
  appending to the same file; `BEGIN IMMEDIATE` serialises appends so the chain never forks
  (tested with two concurrent processes).
* **Flood-resistant:** `SECURITY_REJECT` is rate-limited to 20 entries/minute per type; the
  remainder is recorded as one `SECURITY_REJECT_SUPPRESSED` count so an attacker cannot
  fill the disk or bury other entries.

### Event types

| Event | Writer | Details |
|---|---|---|
| `AGENT_START` / `AGENT_STOP` | collector | pid, chain id, key epoch, blackout schedule / final security stats |
| `LINK_STATE` | collector | from → to (ONLINE, BLACKOUT, RECOVERING), elapsed, telemetry pending |
| `COMMAND` | collector | command id/type, SUCCESS or REJECTED + reason, `issued_by` operator |
| `SECURITY_REJECT` | collector / ground | topic, reason code (REPLAY, AUTH_FAILED, UNENCRYPTED, …) |
| `KEY_ROTATION` | collector | old → new link-key epoch |
| `RESOURCE_SAMPLE` | collector | CPU %, agent RAM MB, process count, SoC °C, board W (every 30 s) |
| `RESOURCE_LIMIT_EXCEEDED` / `_CLEARED` | collector | which limit(s) crossed |
| `AI_START`, `ANOMALY_DETECTED`, `ANOMALY_CLEARED` | AI detector | score, threshold, suspect sensor & subsystem (triage), inference latency |
| `AI_PERFORMANCE` | AI detector | p50/p95/max inference latency per 50 evaluations (target 50 ms) |
| `COMMAND_SENT`, `COMMAND_RESULT`, `AUDIT_INTEGRITY_ALERT` | ground (operator chain) | operator, command id, outcome / alert |

## 3. Ground verification, search and export

`ground/storage/audit_store.py` stores downlinked entries in `flight_audit` inside the run's
ground SQLite file and gives each one an integrity status: `VERIFIED`, `PENDING_LINK`
(predecessor not received yet; resolved automatically), `HASH_MISMATCH`, or `BROKEN_LINK`.
A different entry arriving for an already-stored position is recorded as a conflict. Any
problem prints `AUDIT INTEGRITY ALERT` and is written to the ground operator chain.

```bash
DB=ground/ingestion/demo_ground_<run>.sqlite3
python -m ground.storage.audit_cli --db $DB verify                       # full re-verification
python -m ground.storage.audit_cli --db $DB search --type ANOMALY_DETECTED
python -m ground.storage.audit_cli --db $DB search --text Battery_Voltage --since 2026-11-27
python -m ground.storage.audit_cli --db $DB export --format csv --out audit.csv
python -m ground.storage.audit_cli --operator-log verify                 # ground's own chain
```

Exports include `prev_hash`/`entry_hash`, so an exported chain can be re-verified
independently. `scripts/demo.py` verifies the chain automatically at the end of a run
(`[AUDIT] Chain … INTACT`) and fails the run if it is broken.

## 4. Resource monitoring (`flight/monitoring/resource_monitor.py`)

| Quantity | Source | Limit (PR#1) |
|---|---|---|
| SoC temperature | `/sys/class/thermal/thermal_zone0/temp` (Pi), psutil sensors elsewhere | ≤ 70 °C (REQ-HW-01) |
| Agent RAM | psutil RSS summed over the collector + AI processes | ≤ 2 GB (REQ-HW-02) |
| Board power | `vcgencmd pmic_read_adc` (Pi 5 PMIC, Σ V×I) | ≤ 10 W (REQ-HW-03) |
| CPU load | psutil system CPU % | ≤ 80 % (REQ-FSM-01) |
| Inference latency | timed around each TFLite evaluation | ≤ 50 ms (REQ-ML-01) |

Every 30 s (`--resource-interval`) the collector records a `RESOURCE_SAMPLE`, logs a
`RESOURCE_LIMIT_EXCEEDED` entry and console warning when a limit is crossed, and includes
the latest sample in the `GET_STATUS` command reply. Values a platform cannot provide are
reported as `null`/`n/a` rather than guessed (a laptop has no SoC thermal zone; a Pi 4 has no
PMIC ADC), and the power figure is an on-board estimate — formal REQ-HW-03 acceptance still
uses the inline USB power meter listed in PR#1. On Windows the agent process count is
doubled by the venv launcher stub; this does not materially affect the RAM figure.

## 5. How it was verified

| Requirement | Target | Evidence |
|---|---|---|
| REQ-AUD-01 coverage | every anomaly, state transition, recovery/command, approval, security event | Event table above; live run recorded attacks, signed command, anomaly detect/clear, key rotations, BLACKOUT/RECOVERING |
| REQ-AUD-01 SHA-256 chain, 100 % alterations detected | 100 % | `test_modified_entry_detected`, `test_deleted_entry_detected`, `test_rewritten_history_with_recomputed_hash_detected`, ground `HASH_MISMATCH`/`BROKEN_LINK` test |
| REQ-AUD-01 append-only | — | `test_append_only_triggers_block_update_and_delete` |
| REQ-AUD-01 search ≤ 2 s, export | ≤ 2 s | `test_search_10k_entries_under_2s_and_export` (0.7 ms over 10 000 entries), CSV/JSON export |
| Blackout + normal link | 0 audit entries lost | `test_blackout_buffered_entries_delivered_and_verified` (253 entries, identical chain heads); live run: 19/19 delivered, ground `INTACT` |
| REQ-HW-01/02/03 monitoring | ≤ 70 °C, ≤ 2 GB, ≤ 10 W | `tests/monitoring` (sampling, breach logic, Pi thermal & PMIC parsing); live `RESOURCE_SAMPLE` entries |
| REQ-ML-01 latency | ≤ 50 ms | live `AI_PERFORMANCE`: p50 0.69 ms, p95 1.24 ms (laptop; re-check on the Pi) |

Automated: `python -m pytest`. Live: follow [SECURITY.md §6](SECURITY.md#6-how-it-was-verified);
then run the `audit_cli` commands above against the printed ground SQLite path.

## 6. Limitations

* The chain proves *integrity and order*, not authorship: someone with full control of the
  Pi could rebuild a self-consistent chain from scratch. Ground detects this for anything it
  already holds (conflicts / broken links), which is why entries are downlinked promptly.
* `AGENT_STOP` and entries written after the ground station disconnects stay on the Pi until
  the next contact with the same database; a new demo run starts a new chain.
* No FSM exists yet, so "recovery actions" are audited as commands and AI decisions; when
  the FSM is added it should call `AuditLog.append("RECOVERY_ACTION", …)`.
* Retention (telemetry ≥ 7 days, audit ≥ 1 year, REQ-CLD-01) is unlimited local SQLite here;
  pruning/archiving policy is left to the cloud-storage work.
