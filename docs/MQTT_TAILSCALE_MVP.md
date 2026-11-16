# MQTT over Tailscale — standalone two-computer demonstration

This is **additive**. The prior HTTP store-and-forward demo is untouched. Never run both
flight collectors simultaneously: they would ingest the same telemetry into different queues.

## Architecture

1. Pi: Jay's simulator publishes telemetry to local Mosquitto at `127.0.0.1:1883`.
2. Pi: new MQTT collector stores telemetry and communication events in durable SQLite.
3. Tailscale Serve exposes *only to the private tailnet* the Pi's existing localhost MQTT broker.
4. Mac: the MQTT ground receiver subscribes over Tailscale, persists locally in SQLite,
   and returns application-level ACK messages.
5. Mac: the command CLI sends `GET_STATUS` and `REQUEST_LOGS`; the Pi validates and replies.
6. A software blackout prevents ground-facing MQTT messages/commands, not local sensor ingestion.

## Network setup

- Install Tailscale on Mac and Raspberry Pi, authorize them to the same **trusted tailnet**.
- Verify on Pi: `sudo systemctl is-active mosquitto` (should be `active`).
- On Pi: `tailscale ip -4` (save the Pi's stable Tailscale IPv4 address).
- On Pi: `sudo tailscale serve --bg --tcp=1883 tcp://127.0.0.1:1883`
- On Pi: `tailscale serve status`
- First confirm the **Mac** is signed in to the same Tailscale tailnet and appears in `tailscale status` on the Pi. From Mac: `nc -G 5 -vz PI_TAILSCALE_IP 1883` (expect success).
- This assumes a trusted tailnet. Do not use `tailscale funnel` or public port forwarding.
  For shared access by multiple team members, use Tailscale device sharing/ACLs.

## Code setup

Extract patch into the existing `feature/pi-blackout-store-forward` repo on Mac.
Push commit. In Pi repo, `git pull --ff-only origin feature/pi-blackout-store-forward`.
Python packages already included: `paho-mqtt>=2.1,<3`.

## Run (3 Pi shells + 2 Mac terminals)

Pi flight shell:

```sh
cd ~/Self-healing_Satellite-blackout && source .venv/bin/activate
python -m flight.communications.mqtt_blackout_collector --mqtt-host 127.0.0.1 \
  --blackout-after 10 --blackout-duration 15 --db flight/storage/pi_mqtt_flight.sqlite3
```

Mac ground shell:

```sh
cd "$HOME/Desktop/Dhruvin/Capstone/Self-healing_Satellite" && source .venv/bin/activate
python -m ground.ingestion.mqtt_ground --broker-host PI_TAILSCALE_IP \
  --db ground/ingestion/mac_mqtt_ground.sqlite3
```

Pi simulator shell:

```sh
cd ~/Self-healing_Satellite-blackout && source .venv/bin/activate
python -m simulator --uhf-csv docs/uhf_telemetry.csv --mqtt-host 127.0.0.1 \
  --duration-seconds 45 --run-id "mqtt-$(date +%s)"
```

Mac command shell (while flight collector is ONLINE):

```sh
cd "$HOME/Desktop/Dhruvin/Capstone/Self-healing_Satellite" && source .venv/bin/activate
python -m ground.ingestion.send_command --broker-host PI_TAILSCALE_IP --command GET_STATUS
python -m ground.ingestion.send_command --broker-host PI_TAILSCALE_IP --command REQUEST_LOGS
```

Target: ~630 unique telemetry records and 4 state transitions, pending queues 0,
plus successful response to each read-only command. Repeating a run into the same database
may increase counts, so use a unique SQLite filename per distinct run.

**Limitations:** Software blackout, not RF link physics. MQTT transport does not
replace ECSS/CCSDS telecommand standards. Current ground commands are read-only only,
not real FSM changes, software patch installation or arbitrary remote shell execution.
This lab version relies on Tailscale's device authorization; restrict your tailnet
membership/access. The MQTT broker itself is not configured with separate login credentials.
MQTT QoS 1 is used, plus SQLite-level deduplication and application acknowledgements.

## Cleanup

Use Ctrl+C to stop the running Pi collector and Mac ground receiver.
To stop MQTT tailnet forwarding (does not uninstall Tailscale):
`tailscale serve --tcp=1883 off`.
