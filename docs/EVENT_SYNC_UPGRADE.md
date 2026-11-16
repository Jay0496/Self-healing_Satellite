# Blackout event-log synchronization (MVP extension)

This extension preserves the completed telemetry store-and-forward implementation and adds acknowledged, duplicate-resistant event-log forwarding.

- Flight state changes are durably stored in the Pi SQLite `events` table with `event_uid`, `event_time`, `state`, and `message`.
- The previous SQLite schema is migrated automatically without deleting event history.
- During the simulated blackout, both telemetry and event records remain on the Pi.
- When communication resumes, the flight collector delivers telemetry to `/ingest` and events to `/ingest-events`.
- The receiver stores each unique event by `event_uid` and responds with `acknowledged_event_uids`. Only acknowledged events are marked delivered on the Pi.
- The ground `/status` endpoint exposes `received_records` and `received_events`.

## Verification (all three programs on Pi)

Open three Raspberry Pi Connect remote shells. Run each command from `~/Self-healing_Satellite-blackout` after `source .venv/bin/activate`.

1. Shell 1: `python -m ground.ingestion.receiver --host 127.0.0.1 --db ground/ingestion/pi_events_ground.sqlite3`
2. Shell 2: `python -m flight.communications.blackout_collector --mqtt-host 127.0.0.1 --blackout-after 10 --blackout-duration 15 --ground-url http://127.0.0.1:8765/ingest --db flight/storage/pi_events_flight.sqlite3`
3. Shell 3: `python -m simulator --uhf-csv docs/uhf_telemetry.csv --mqtt-host 127.0.0.1 --duration-seconds 45 --run-id "pi-events-$(date +%s)"`
4. After simulator exits: `curl http://127.0.0.1:8765/status`
5. Expected on a clean demo database: `received_records` 630, `received_events` 4 (ONLINE, BLACKOUT, RECOVERING, ONLINE). Both flight queues should have zero pending acknowledgements. Real sample counts can vary if MQTT input is missed.

## Important boundaries

- Blackout is a deterministic application-level simulation, **not** an actual RF outage.
- The Pi and receiver run on the same machine for this test; cross-house Pi-to-Mac communication is not yet demonstrated.
- Plain HTTP is for localhost/trusted isolated demonstration. Do not forward port 8765 onto the public internet.
- A production-grade system would add authentication, encryption, bounded retention, replay protection, and reboot/restart validation.
