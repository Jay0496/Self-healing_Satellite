# Simulator

Development and test tooling that produces satellite-like inputs: sensor-shaped telemetry streams, controlled faults, and simulated link conditions. It is **not** part of the flight runtime or ground production services.

| Directory | Responsibility |
| --- | --- |
| `replay/` | Replay recorded telemetry at configurable rates |
| `fault_injection/` | Repeatable fault scenarios with labelled ground truth |
| `link/` | Blackouts, bandwidth limits, and other link conditions |
| `tests/` | Repeatability tests for the above |

## Ground rules

1. **Every stream belongs to a sensor.** MQTT payloads carry a sensor id and a physical quantity. Anonymized ESA channel ids (`channel_41`, `subsystem_5`) never appear as stream names.
2. **ESA and UHF data are source material, not identities.** They supply shape, cadence, drift, and nominal behaviour. They do not establish that a channel *is* a given sensor.
3. **Provenance is mandatory.** Every record states where its value came from (`real_replay`, `derived`, `generated_mock`, `fault_injected`).
4. **Fault injection happens after generation**, so ground truth stays reproducible and separable from the baseline stream.
5. **Generated data never trains the anomaly model.** It exists to exercise the streaming path, the Pi-side sensor adapter, and the fault/recovery loop.

---

## 1. Sensor streams and their data basis

The project monitors one BMP-class barometer, one INA219 power monitor, and one IMU. Humidity is out of scope.

The table below is the answer to "what do we need to generate each stream, and what is purely generated".

| Sensor | Stream field | Unit | Data needed as input | What the real data contributes | Purely generated |
| --- | --- | --- | --- | --- | --- |
| BMP-class | `temperature_c` | °C | UHF `NanoMind_Temp` (sparse) | Slow ambient drift, day-scale wander, sparse sampling gaps | Absolute calibration, realistic °C range, sensor noise, quantisation |
| BMP-class | `pressure_hpa` | hPa | none | — | **Everything**: slow pressure drift, orbital/thermal variation, noise |
| INA219 | `bus_voltage_v` | V | UHF `Battery_Voltage` (sparse) | Envelope, step changes, discharge/recovery shape | Absolute rail voltage, ripple, load-dependent sag |
| INA219 | `current_ma` | mA | none | — | **Everything**, constrained so `power ≈ voltage × current` |
| INA219 | `power_mw` | mW | generated voltage + current | — | **Derived** from the two generated values (not an independent stream) |
| IMU | `gyro_x/y/z_dps` | °/s | UHF `X/Y/Z_Coarse_Spin` (sparse) | Coarse rotation pattern and relative axis behaviour | High-rate waveform, noise, bias drift, sample timing |
| IMU | `accel_x/y/z_g` | g | none | — | **Everything**: gravity projection, body-rate coupling, vibration, noise |
| IMU | `roll/pitch/yaw_deg` | ° | generated gyro | — | **Derived** by integration; label as derived, never as a measured field |

Two consequences worth stating explicitly:

- **Pressure, current, acceleration, and orientation have no real source** in either dataset. Nothing is "replayed" for them.
- **Temperature, voltage, and coarse spin have a real but sparse shape reference only.** The UHF export covers roughly three weeks (2024-05-01 → 2024-05-22) with about 4–5% channel coverage, raw unscaled integer values, and multi-day gaps. That is enough to imitate drift and step behaviour, not to calibrate units.

### 1.1 BMP-class barometer

- Temperature: take the UHF `NanoMind_Temp` trace (665 non-null samples, raw range 219–448) as a **drift template**, rescale to a plausible operating range, then add sensor-specific noise and quantisation. Do not present the raw values as °C without a documented transform id.
- Pressure: generated from scratch — a slow baseline with orbital/thermal periodic terms plus noise. Never derive pressure from an anonymized ESA channel and call it pressure.
- Humidity: not generated; excluded from the stream schema.

### 1.2 INA219 power monitor

- Voltage: use UHF `Battery_Voltage` (761 samples, raw 6967–8213, plausibly millivolts ≈ 7.0–8.2 V but unverified) for envelope and step behaviour; choose the absolute rail value in config.
- Current: generated. Draw profile should respond to a simple load model (idle, compute burst, radio transmit) so faults have something meaningful to disturb.
- Power: computed as `V × I` and marked `derived`. Do not generate power independently — an inconsistent V/I/P triple is an immediate tell.

### 1.3 IMU

- Coarse rotation: UHF `X/Y/Z_Coarse_Spin` (761 samples each, ≈4.6% coverage) supplies the low-frequency rotation pattern and the relative magnitude between axes. Expand it to a high-rate gyro signal with noise and bias.
- Linear acceleration: generated from gravity projection plus body-rate coupling and vibration.
- Orientation: integrate the generated gyro, or leave it out. Mark `derived`.

### 1.4 Context / mode stream

Operating mode (e.g. nominal, payload, safe) is a state signal, not a sensor reading. Generate it from a small, documented enum so fault scenarios can be conditioned on it. Do not lift anonymized ESA categorical channels into a named mode stream.

---

## 2. Source data inventory

| Source | Provides | Volume / cadence | Notes |
| --- | --- | --- | --- |
| `uhf_telemetry.csv` (OPS-SAT UHF export) | `NanoMind_Temp`, `Battery_Voltage`, `X/Y/Z_Coarse_Spin`, `ADCS_mode` | 16,500 rows, 22 columns, asynchronous, ≈4–5% coverage per reference channel | 2024-05-01 → 2024-05-22; duplicate timestamps and out-of-order rows exist |
| ESA Mission 1, group 8 (`channel_41`–`channel_46`) | Anonymous telemetry, 30 s cadence | 15,381,169 samples each, 2000-01-01 → 2013-12-31 | Anomaly-model benchmark; **not** sensor-named |
| ESA Mission 1 `channel_66` | Anonymous scalar telemetry, ≈158 s cadence | 4,810,995 samples | Baseline stream; not sensor-named |
| ESA Mission 2 `channel_13`, `channel_20` | Anonymous telemetry, 18 s cadence | 7,387,262 samples each, 2000 → mid-2003 | Comparison streams only |

Practical limits to design around:

- UHF reference channels are too sparse to drive a 1 Hz stream directly; they provide a **template**, and the generator interpolates/parameterises from them.
- ESA archives are read through range requests (`remotezip` + `zipfile-deflate64`); the generator should consume small exported extracts (Parquet/CSV), not the 3.5 GiB archives.

### 2.1 Optional raw replay

Replaying an ESA channel as-is is allowed for throughput, buffering, and back-pressure tests. When doing so:

- keep the original timestamps and 18 s / 30 s / 158 s cadence,
- tag the record `real_replay` and keep the source channel id in metadata, never in the stream name,
- do not describe the replayed channel as any named sensor.

---

## 3. Cadence and rates

| Stream | Native cadence | Recommended publish rate |
| --- | --- | --- |
| Generated sensor streams | — | 1 Hz (configurable), all fields aligned on the same tick |
| UHF-driven shape reference | ≈20–30 s, with multi-day gaps | used to derive parameters, not published raw |
| ESA replay (optional) | 18 s / 30 s / 158 s | native cadence; repeats marked `held` |

The 10 records/s requirement applies to **replay/output throughput**, not to native sensor sampling. If the engine publishes faster than a source produces, repeat the latest value, keep its original measurement time, and mark it `held`. Never interpolate a sparse signal into a dense one and present it as measured.

---

## 4. Record contract

Every published record should carry at least:

| Field | Purpose |
| --- | --- |
| `sensor_id` | e.g. `bmp0`, `ina219`, `imu0` |
| `signal` | e.g. `temperature_c`, `current_ma`, `gyro_z_dps` |
| `value`, `unit` | Reading and physical unit |
| `measurement_time` | When the value is valid (source time, not send time) |
| `publish_time` | When it was emitted |
| `source` | Dataset/channel or `generator` |
| `transform_id` | Link to the generation parameters when rescaled from a template |
| `provenance` | `real_replay` / `derived` / `generated_mock` / `fault_injected` |
| `quality` | `observed` / `held` / `interpolated` / `missing` / `stale` |
| `scenario_id` | Present only while a fault scenario is active |

Everything above is metadata; the fault injector should only change `value` (and the provenance/quality flags), never the timestamps used for ground truth.

---

## 5. Fault-injection boundary

Order of operations, and it matters:

```
source data → generator (nominal stream) → fault injector → MQTT → Pi / detector
                     │                          │
                     └── baseline record        └── scenario id + injection window
```

- Injection is deterministic given `(scenario_id, seed)`.
- Record the affected `sensor_id`/`signal`, fault type, start, end, magnitude, and seed.
- Keep the nominal stream reproducible so the same input can be replayed with and without a fault.
- Pi-side injection belongs in the sensor adapter/shim, not in the detector, so both paths can be compared.

---

## 6. What must not be claimed

- That any anonymized ESA channel *is* temperature, pressure, voltage, current, or acceleration.
- That pressure, current, or linear acceleration is replayed from real data — they are generated.
- That the generated stream is 10 Hz native telemetry.
- That model performance measured on the ESA benchmark transfers to the generated sensor stream. That transfer has to be tested, and it is a separate result.

---

## 7. Reproducibility checklist

- [ ] Generator config (ranges, noise, seeds, transforms) stored with each run.
- [ ] One `scenario_id` per fault, with a matching ground-truth row.
- [ ] Same seed + same config ⇒ byte-identical stream.
- [ ] Generated and replayed data written to separate storage prefixes from model training data.
- [ ] Cadence and `held` semantics asserted in tests.
