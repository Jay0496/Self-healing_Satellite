# Communication blackout/store-and-forward MVP

Add this patch to a **new feature branch based on `feature/simulator`**.
This does **not** modify Jay's simulator code. It reuses his MQTT telemetry stream and shared `TelemetryRecord` protocol.

## Architecture

```
UHF CSV -> Jay's simulator -> LOCAL MQTT broker -> Pi flight collector -> Pi SQLite
                                                               |
                                             SIMULATED GROUND LINK BLACKOUT
                                                               |
                                                   HTTP POST -> Mac ground receiver
                                                               |
                                                     Ground SQLite + acknowledgements
```

The simulator and broker should run on the Pi for the real demonstration. The ground receiver runs on the Mac.
The blackout is a deliberate, deterministic software gate on the **Pi-to-ground** HTTP link; it does not disable Wi-Fi or SSH.

## Setup from repository root

1. Place real UHF file at `docs/uhf_telemetry.csv` (required by simulator). Source: https://opssat.esa.int/ops-sat-1/docs/uhf_telemetry.zip. It must contain `Timestamp`, `NanoMind_Temp`, `Battery_Voltage`, `X_Coarse_Spin`, `Y_Coarse_Spin`, `Z_Coarse_Spin`.
2. Create Python 3.10+ venv: `python3 -m venv .venv && source .venv/bin/activate`
3. Install: `python -m pip install -r simulator/requirements.txt`
4. Preview: `python -m simulator --dry-run --frames 1`
5. Start a LOCAL Mosquitto broker at 127.0.0.1:1883.
6. Open separate terminals and activate venv + `cd` repository root in EACH:
   - Ground (Mac): `python -m ground.ingestion.receiver --host 127.0.0.1`
   - Flight (same Mac for first trial): `python -m flight.communications.blackout_collector --blackout-after 20 --blackout-duration 60`
   - Simulator (same Mac for first trial): `python -m simulator --mqtt-host 127.0.0.1 --duration-seconds 120 --run-id demo-$(date +%s)`
7. Observe state transitions and verify `/status` in a browser: http://127.0.0.1:8765/status
8. Observe local and ground SQLite results using Python sqlite3 if needed; `.sqlite3*` files should NOT be committed.

## Raspberry Pi

- Install `mosquitto` and `python3-venv` on the Pi; run Mosquitto locally.
- Run the flight collector on Pi with `--ground-url http://MAC_LAN_IP:8765/ingest`.
- Run the simulator on Pi, pointed at localhost broker.
- Run the ground receiver on the Mac with `--host 0.0.0.0` on a trusted local Wi-Fi network only. Ensure both devices can reach each other and that the Mac's firewall permits the connection.
- Do not disconnect Pi Wi-Fi during simulation! Ground connectivity is gated in software.

## Guarantees and limitations

- SQLite persists samples and marks them acknowledged **after ground's JSON acknowledgement**.
- Ground deduplicates by `sample_id` so retries do not create duplicates.
- Sensor input is `generated_mock`/`derived` from Jay's generator, not measured Pi GPIO sensors.
- The outage uses a known schedule; it does **not** predict orbital coverage or detect RF loss.
- Transport is unencrypted HTTP and should remain on a trusted demo LAN, not the Internet.
- This is an MVP; add backpressure, disk limits, authentication, and monitoring later.
