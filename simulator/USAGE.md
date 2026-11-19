# Simulator usage

The simulator creates virtual BMP-class, INA219, and IMU readings and publishes them **only to the flight telemetry MQTT topic tree**. It does not read physical sensor hardware, connect to ground services, simulate blackouts, or inject faults. Every generated record passes through a pass-through transformation interface so future fault-manipulation work can be added before publishing.

## Install

From the repository root, install the MQTT publisher dependency:

```powershell
python -m pip install -r simulator/requirements.txt
```

The broker must be reachable at the configured host and port. Start the team's local Mosquitto broker separately; no broker or ground service is bundled with the simulator.

## Preview generated records without MQTT

```powershell
python -m simulator --dry-run --frames 2
```

This prints one JSON line per sensor signal. To use a different seed or UHF CSV:

```powershell
python -m simulator --dry-run --frames 5 --seed 7 --uhf-csv docs/uhf_telemetry.csv
```

## Stream to flight over MQTT

```powershell
python -m simulator --mqtt-host localhost --mqtt-port 1883 --duration-seconds 30
```

The default topic prefix is `satellite/flight/telemetry`; each reading is published to:

```text
satellite/flight/telemetry/{sensor_id}/{signal}
```

The default generated cadence is 1 Hz **per signal** (14 records are emitted per tick when orientation is enabled). Use `--signals` to select channels for PR2's 10 records/s throughput test; this does not claim native sensor sampling at 10 Hz:

```powershell
python -m simulator --rate-hz 10 --signals bus_voltage_v --duration-seconds 5
```

Use `--duration-seconds 0` (the default) to stream until Ctrl+C. `SIMULATOR_MQTT_HOST`, `SIMULATOR_MQTT_PORT`, `SIMULATOR_MQTT_TOPIC_PREFIX`, `SIMULATOR_MQTT_QOS`, `SIMULATOR_MQTT_CLIENT_ID`, `SIMULATOR_MQTT_USERNAME`, and `SIMULATOR_MQTT_PASSWORD` may provide local configuration. The topic prefix must remain under a `flight` path; ground topics are rejected.

## Data-generation behavior

- `NanoMind_Temp`, `Battery_Voltage`, and `X/Y/Z_Coarse_Spin` are used only as shape templates from the OPS-SAT UHF CSV. Generated values are rescaled to configured sensor ranges and tagged `generated_mock` with a transform ID.
- Pressure, current, and acceleration use deterministic programmatic models. Current follows nominal load phases; voltage and current produce derived power (`mW = V × mA`). Optional orientation is integrated from gyro values and tagged `derived`.
- UHF timestamps are sorted, duplicate numeric samples are combined by median, and normalized with robust 1st/99th percentile bounds. Interpolation is limited to gaps no larger than the configured template gap. Long/missing regions use the seeded nominal fallback model.
- `measurement_time` and `publish_time` use the deterministic simulator clock. The MQTT scheduler separately paces publication against host time.
- The transformation interface currently uses identity/pass-through only. Fault transforms, trigger listeners, ESA data, ground communication, and blackout simulation are not implemented.
- The UHF CSV is a local input at `docs/uhf_telemetry.csv`; it is not bundled in the simulator package. Supply that file when running outside this repository checkout.

## Tests

From the repository root:

```powershell
python -m pytest simulator/tests -q
```

The publisher tests use an injected fake MQTT client and need no running broker. A live broker/flight consumer integration test can be added once the flight telemetry MQTT subscriber contract is implemented.
