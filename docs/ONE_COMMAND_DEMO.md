# One-command Mac + Raspberry Pi capstone demo

Use **one terminal on each machine**, not four. The launcher supervises the same four
already-tested applications. It adds summary output; it does not change MQTT topics,
telemetry schemas, model inference, or the simulated blackout mechanism.

## Prerequisites

- Raspberry Pi Mosquitto active and Tailscale Serve forwarding port 1883 to localhost.
- Mac logged into the same Tailscale network; Pi Tailscale address `100.83.109.119`
  (change `--broker-host` if this address changes).
- Mac has a working `.venv` with `paho-mqtt`; Pi `.venv` has `paho-mqtt`,
  `numpy`, and `ai-edge-litert`.
- Mac only: `docs/uhf_telemetry.csv` must exist locally. **Do not commit this CSV**.
- On both machines, use the same Git branch/commit, preferably `main` after merge.

## 1. Start Raspberry Pi (Raspberry Pi Connect — one shell)

```bash
cd ~/Self-healing_Satellite-integration
# If needed, first: git pull origin feature/full-capstone-integration
bash scripts/start_pi.sh
```

The launcher starts the blackout collector and AI detector together. It should say
`[READY] Pi services are subscribed. Start the Mac launcher NOW.` The blackout starts
65 seconds after the first sensor reading and lasts 60 seconds.

## 2. Start Mac (one Terminal window)

```bash
cd "$HOME/Desktop/Dhruvin/Capstone/Self-healing_Satellite-model13"
# If needed, first: git pull origin feature/full-capstone-integration
bash scripts/start_mac.sh
```

The Mac launcher starts the ground receiver, waits until it is subscribed, and only
then starts the simulator. By default it sends 36 model13 frames, 13 signals each,
with the reproducible 4200 mV battery fault starting at tick 15. It reports fault
messages, ground delivery totals, and the saved SQLite path. It stops both Mac
processes automatically after delivery is verified (or after the sync timeout).

## 3. Stop Raspberry Pi

When Mac prints `[SUCCESS] All expected telemetry reached ground`, press `Ctrl+C`
in the **Pi** shell. It stops the two Pi processes and prints SQLite totals,
pending records and AI alert summary.

## Optional random-fault mode

For a random spike/noise/drift/stuck-at sequence, start Pi the same way and use:

```bash
bash scripts/start_mac.sh --mode random
```

Random faults have 5% chance of starting per frame in model13 mode; a short run may
have no injected faults and not every injected fault triggers the AI threshold.
For the repeatable four-feature demo use the default `demo` mode.

## Expected demonstration

- Mac simulator publishes 36 x 13 = 468 telemetry records to `satellite/flight/telemetry/#`.
- The Pi AI subscribes to local telemetry and independently reports NOMINAL/ANOMALY.
- Pi collector stores records in SQLite. BLACKOUT disables downlink only, not local
  MQTT or AI. Recovery replays pending telemetry and link events.
- The Mac ground receiver subscribes to `capstone/g29/pi01/downlink/#`, commits
  unique samples/events in SQLite and sends application ACKs to the Pi.
- Pi final pending count should reach 0; Mac shows all 468 records and 4 link events.

**Limitations:** AI decisions currently print on the Pi only; no AI-event downlink,
FSM-controlled recovery or physical RF link is included. The script consolidates
terminal output but does not implement those missing features.

## Troubleshooting

- `MQTT broker unreachable`: verify Mac Tailscale connection, Pi Mosquitto and Serve.
- `Missing telemetry CSV`: copy `uhf_telemetry.csv` into Mac clone's `docs/` folder.
- `Pi services did not subscribe`: check Python dependencies and existing collector/AI
  processes; don't run older four-terminal processes at the same time.
- Ground fewer than 468 or pending nonzero: verify Mac receiver stayed running,
  Pi link returned ONLINE, and both devices remained connected to the tailnet.
- To use different blackout timings, run `bash scripts/start_pi.sh --blackout-after 65 --blackout-duration 60`.
