# Shared Protocol

This module is for versioned message formats, packet envelopes, and protocol-level interfaces shared by flight, ground, and simulation tooling. Endpoint-specific connection behavior, keys, and runtime behavior belong with their owning component.

## Telemetry record v1

`telemetry.py` defines `TelemetryRecord`, the shared JSON representation for one sensor signal at one sample time. The simulator emits one MQTT message per record; records from the same generated sensor tick have the same `measurement_time` and distinct `sample_id`/`sequence` values.

Required fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Protocol version; currently `1`. |
| `run_id`, `sample_id`, `sequence` | Run identity and stable, ordered record identity. |
| `sensor_id`, `signal`, `value`, `unit` | Sensor-shaped signal, numeric value, and physical unit. |
| `measurement_time`, `publish_time` | Timezone-aware ISO-8601 timestamps; the simulator emits UTC. |
| `source`, `transform_id` | Generator/template source and named transform. |
| `provenance` | `generated_mock`, `derived`, `real_replay`, or reserved `fault_injected`. |
| `quality` | `observed`, `held`, `interpolated`, `missing`, or `stale`. |

`TelemetryRecord.validate()` enforces the version, finite numeric value, supported metadata values, and timezone-aware timestamps. `to_json()` produces stable compact JSON; `from_json()` validates a received JSON record. `shared/protocol/` owns this wire contract only; MQTT topic selection and broker configuration remain with the simulator/flight endpoints.
