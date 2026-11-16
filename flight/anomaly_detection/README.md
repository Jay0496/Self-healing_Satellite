# Onboard Dual-Stage Telemetry Anomaly Detection & Triage (`flight/anomaly_detection`)

This module implements the **Phase 1 Edge Machine Learning Pipeline** (Concept 2 + Concept 5) for the **Autonomous Mission Control Agent for Self-healing IoT Satellites**. It executes onboard the Raspberry Pi to detect subtle multivariate telemetry degradation in real time during orbital ground blackouts and isolate the failing spacecraft subsystem for the Deterministic Finite State Machine (FSM).

---

## Architecture Overview

1. **Stage 1 — Quantized INT8 1D-CNN Autoencoder + Nonparametric Dynamic Thresholding (NDT):**
   * Trained strictly on nominal multi-subsystem telemetry passes from the ESA **OPS-SAT-1** UHF dataset (`uhf_telemetry.csv`, 16,500 raw records segmented into contiguous ground passes and resampled to a unified 5-second cadence).
   * Compresses 10-step $\times$ 13-channel sliding windows (`50 s` temporal context) through a 16-dimensional latent bottleneck and reconstructs the nominal signal.
   * Normalizes per-channel Mean Squared Error (MSE) residuals against validation baseline medians and 95th-percentile spreads, applying a 3-window moving average against an Extreme Value Theory (EVT) upper threshold (`13.9811`) to prevent false alarms during orbital sun/eclipse transitions.
   * Exported via TensorFlow Lite Post-Training INT8 Quantization (`model_int8.tflite`, `25.9 KB`) for low-latency ARM CPU execution.

2. **Stage 2 — Lightweight Onboard Triage Agent (Concept 5):**
   * When Stage 1 triggers an anomaly (`is_anomaly == True`), Stage 2 ranks the normalized per-channel reconstruction residuals (`argmax`) to identify the primary suspect sensor and maps it to its physical spacecraft subsystem (`EPS_POWER`, `UHF_COMMS`, `ADCS_ATTITUDE`, `ADCS_SUN_SENSORS`).
   * Passes structured fault context to the Deterministic FSM to trigger targeted sub-component recovery rather than a full system reboot.

---

## Directory Structure

```text
flight/anomaly_detection/
├── README.md
├── edge_detector.py              # Runtime Raspberry Pi Stage 1 + Stage 2 inference wrapper
├── artifacts/                    # Pre-trained INT8 model & runtime calibration constants
│   ├── model_int8.tflite         # Quantized INT8 1D-CNN Autoencoder (25.9 KB)
│   ├── scaler_params.json        # RobustScaler center/scale, feature order & training medians
│   └── threshold_config.json     # NDT threshold, per-channel residual bounds & subsystem map
├── training/                     # Offline preprocessing, training & INT8 quantization scripts
│   ├── 01_preprocess_and_eda.py  # Pass segmentation, 5s resampling, EDA plots & windowing
│   └── 02_train_and_quantize.py  # Model training, synthetic fault injection & INT8 export
├── figures/                      # Exploratory Data Analysis (EDA) visualizations for reports
│   ├── 01_raw_completeness.png
│   ├── 02_correlation_heatmap.png
│   └── 03_nominal_pass_sample.png
└── data/                         # Local-only dataset directory (git-ignored)
    ├── uhf_telemetry.csv
    └── processed_windows.npz
```

---

## Phase 1 Quantitative Verification Results

Evaluated using the quantized `model_int8.tflite` interpreter against held-out nominal OPS-SAT-1 passes and synthetic programmatic fault profiles (multiplicative battery voltage gain sags, additive thermal drift ramps, and ADCS sensor freeze/spikes):

| Target ID | Metric | Achieved Result | Acceptance Criterion | Status |
| :--- | :--- | :--- | :--- | :--- |
| **T03** | Event-Level $F_1$-Score | **0.8922** | $\ge 0.85$ | **PASS** |
| **T03** | False Positive Rate (FPR) | **0.00%** | $< 3.00\%$ | **PASS** |
| **T03** | Precision / Recall | **1.0000 / 0.8054** | Balanced event detection | **PASS** |
| **Concept 5** | Stage 2 Top-1 Sensor Isolation Accuracy | **100.0%** | Granular subsystem triage | **PASS** |
| **T04** | Inference Latency (`p50` / `p95` / `p99`) | **0.003 / 0.004 / 0.004 ms** | $\le 100\text{ ms}$ per window | **PASS** |

---

## Raspberry Pi Runtime Integration

### Dependencies
On the Raspberry Pi (ARM64), lightweight TFLite runtime packages are sufficient (full `tensorflow` is not required):
```bash
pip install numpy ai-edge-litert
# Or fallback: pip install numpy tflite-runtime
```

### Usage in the Flight MQTT / FSM Loop
Import `EdgeAnomalyDetector` in your telemetry ingestion service and pass each incoming telemetry dictionary to `push_and_evaluate()`:

```python
from anomaly_detection.edge_detector import EdgeAnomalyDetector

# Initialize once at flight software startup
detector = EdgeAnomalyDetector()

# Call on every incoming telemetry record (5s cadence / MQTT replay)
result = detector.push_and_evaluate(telemetry_packet)

if result["status"] == "WARMING_UP":
    # Buffering initial 10 steps (50s window) before inference begins
    pass
elif result["is_anomaly"]:
    # Route granular subsystem diagnosis to the Deterministic FSM
    fsm.handle_fault(
        subsystem=result["suspect_subsystem"],
        sensor=result["suspect_sensor"],
        score=result["anomaly_score"]
    )
```

### Input Telemetry Schema (`telemetry_packet`)
`push_and_evaluate()` expects a Python `dict` containing any subset of the 13 monitored channels (missing keys in asynchronous packets are automatically imputed via `scaler_params.json`):
* **EPS / Power:** `Battery_Voltage` ($\text{mV}$), `Battery_Temp` ($\text{deci-}^\circ\text{C}$)
* **UHF Comms:** `NanoCom_Temp` ($\text{deci-}^\circ\text{C}$), `Background_RSSI` ($\text{dBm}$)
* **ADCS Attitude & Sun Sensors:** `X_Coarse_Spin`, `Y_Coarse_Spin`, `Z_Coarse_Spin` ($\text{rad/s}$), `PD1_CSS_theta` through `PD6_CSS_theta` ($\text{rad}$)

### Output Dictionary Format
```json
{
  "status": "ANOMALY",
  "is_anomaly": true,
  "anomaly_score": 54.8438,
  "threshold": 13.9811,
  "suspect_sensor": "Battery_Voltage",
  "suspect_subsystem": "EPS_POWER",
  "top_channel_z": 59.3242
}
```

---

## Retraining & Reproduction (Development Workstation)

To reproduce the preprocessing, EDA charts, and quantized model artifacts from `uhf_telemetry.csv`:

```bash
python3 -m venv venv
source venv/bin/activate
pip install numpy pandas scikit-learn matplotlib seaborn tensorflow

python flight/anomaly_detection/training/01_preprocess_and_eda.py
python flight/anomaly_detection/training/02_train_and_quantize.py
python flight/anomaly_detection/edge_detector.py
```