# Data Exploration

Colab-ready, read-only screening of the two telemetry sources used by the anomaly-detection
workstream:

- **UHF export** (`uhf_telemetry.csv`, OPS-SAT): named but sparse channels
  (`NanoMind_Temp`, `Battery_Voltage`, `X/Y/Z_Coarse_Spin`). Retained as a semantic reference
  for streaming/mock-sensor work only.
- **Zenodo "ESA Anomaly Dataset"**: three anonymized mission archives (~10.8 GiB total). Read
  through HTTP range requests with `remotezip` + `zipfile-deflate64`, so the archives are never
  downloaded in full.

## `data_exploration.ipynb`

The notebook documents *how the model dataset was chosen*. It does not train a model, generate
data, or modify the source archives. Part C records the final selection and the reasoning.

| Part | Content |
| --- | --- |
| A | UHF schema, coverage, and reference interpretation |
| B1 | Zenodo metadata and archive access (Deflate64 + remote range reads) |
| B2 | Catalog screening: 224 entries, 129 `Target=YES`, 170 continuous, 105 continuous targets |
| B3 | Channel profiling: volume, cadence, range, annotation coverage, labelled vs unlabelled behaviour, rate of change |
| B4 | Group checks: intra-group correlation and shared label windows |
| C | Final data selection, exclusions, handling rules, and handoff checklist |

Selected for the anomaly-detection model:

- **Primary:** Mission 1 group 8 (`channel_41`–`channel_46`) — 15,381,169 samples each at 30 s
  cadence; 98 group-level `Anomaly` windows, 294 `Rare Event`, 4 `Communication Gap`.
- **Scalar fallback:** Mission 1 `channel_41`.
- **Secondary:** Mission 2 `channel_13`, `channel_20` (18 s cadence, near-constant level,
  anomalies visible mainly as rate change).
- **Baseline:** Mission 1 `channel_66` (deliberately easy zero-related anomalies).
- **Reserve:** Mission 2 `channel_24`.

Excluded from model work: Mission 2 `channel_99`/`channel_100` (clipped / too sparse),
Mission 3 (no continuous targets), the UHF export (≈4–5% coverage), generated mock values, and
Raspberry Pi sensor readings. Generated values are for MQTT streaming and fault injection only.

The catalog is anonymized by design, so channels are described as "anonymized ESA telemetry with
benchmark annotations" — not as BMP280, INA219, or IMU data. `Target` is an anomaly-benchmark
flag, not a sensor-type label.

## `uhf_zenodo_channel_review.ipynb`

Earlier UHF-only review of the same export. Kept for provenance; `data_exploration.ipynb`
supersedes it.

Keep source datasets and extracted copies outside the repository.
