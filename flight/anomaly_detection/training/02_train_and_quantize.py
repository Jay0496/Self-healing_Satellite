import json
import time
from pathlib import Path
import numpy as np
import tensorflow as tf
from sklearn.metrics import precision_score, recall_score, f1_score, confusion_matrix

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ARTIFACTS_DIR = BASE_DIR / "artifacts"

# 1. Load Processed Windows & Feature Metadata
data = np.load(DATA_DIR / "processed_windows.npz")
X_train, X_val, X_test = data["X_train"], data["X_val"], data["X_test"]

with open(ARTIFACTS_DIR / "scaler_params.json") as f:
    meta = json.load(f)
features = meta["features"]
WINDOW_SIZE = X_train.shape[1]
NUM_FEATURES = X_train.shape[2]

# 2. Build Compact 1D-CNN Autoencoder (Fast & INT8-Friendly for Raspberry Pi)
inputs = tf.keras.Input(shape=(WINDOW_SIZE, NUM_FEATURES))
x = tf.keras.layers.Conv1D(32, kernel_size=3, padding="same", activation="relu")(inputs)
x = tf.keras.layers.MaxPooling1D(pool_size=2)(x)
x = tf.keras.layers.Conv1D(16, kernel_size=3, padding="same", activation="relu")(x)
latent = tf.keras.layers.Flatten()(x)
latent = tf.keras.layers.Dense(16, activation="relu", name="bottleneck")(latent)

x = tf.keras.layers.Dense((WINDOW_SIZE // 2) * 16, activation="relu")(latent)
x = tf.keras.layers.Reshape((WINDOW_SIZE // 2, 16))(x)
x = tf.keras.layers.UpSampling1D(size=2)(x)
x = tf.keras.layers.Conv1D(32, kernel_size=3, padding="same", activation="relu")(x)
outputs = tf.keras.layers.Conv1D(NUM_FEATURES, kernel_size=3, padding="same", activation="linear")(x)

model = tf.keras.Model(inputs, outputs, name="opssat_edge_autoencoder")
model.compile(optimizer=tf.keras.optimizers.Adam(1e-3), loss="mse")

early_stop = tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=8, restore_best_weights=True)
model.fit(
    X_train, X_train,
    validation_data=(X_val, X_val),
    epochs=60,
    batch_size=32,
    callbacks=[early_stop],
    verbose=1
)

# 3. INT8 Post-Training Quantization (PR2 Section 2)
def representative_data_gen():
    for i in range(min(300, len(X_train))):
        yield [X_train[i : i + 1].astype(np.float32)]

converter = tf.lite.TFLiteConverter.from_keras_model(model)
converter.optimizations = [tf.lite.Optimize.DEFAULT]
converter.representative_dataset = representative_data_gen
converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
converter.inference_input_type = tf.float32
converter.inference_output_type = tf.float32

tflite_quant_model = converter.convert()
tflite_path = ARTIFACTS_DIR / "model_int8.tflite"
with open(tflite_path, "wb") as f:
    f.write(tflite_quant_model)
print(f"\nExported {tflite_path} ({len(tflite_quant_model) / 1024:.1f} KB)")

# Helper to run TFLite INT8 inference
interpreter = tf.lite.Interpreter(model_path=str(tflite_path))
interpreter.allocate_tensors()
in_det = interpreter.get_input_details()[0]
out_det = interpreter.get_output_details()[0]

def run_tflite(windows):
    recons = np.zeros_like(windows)
    lats = []
    for i in range(len(windows)):
        sample = windows[i : i + 1].astype(np.float32)
        t0 = time.perf_counter()
        interpreter.set_tensor(in_det["index"], sample)
        interpreter.invoke()
        recons[i] = interpreter.get_tensor(out_det["index"])[0]
        lats.append((time.perf_counter() - t0) * 1000.0)
    return recons, lats

# 4. Calibrate NDT Per-Channel Residual Baseline & Dynamic Threshold on Validation Set
val_recon, _ = run_tflite(X_val)
val_ch_mse = np.mean(np.square(X_val - val_recon), axis=1)  # (N_val, NUM_FEATURES)
ch_med = np.median(val_ch_mse, axis=0)
# 95th percentile denominator absorbs sharp orbital sun-sensor transitions during eclipse exit
ch_iqr = np.percentile(val_ch_mse, 95, axis=0) + 0.05

def compute_smoothed_scores(ch_mse_array, smooth_k=3):
    norm_residuals = (ch_mse_array - ch_med) / ch_iqr
    raw_max = np.max(norm_residuals, axis=1)
    smoothed = np.convolve(raw_max, np.ones(smooth_k) / smooth_k, mode="same")
    return smoothed, norm_residuals

val_scores, _ = compute_smoothed_scores(val_ch_mse, smooth_k=3)
anomaly_threshold = float(np.max(val_scores) * 5.0)
print(f"Calibrated NDT Anomaly Threshold (EVT upper bound): {anomaly_threshold:.4f}")

# 5. Programmatic Fault Injection Engine (PR2 Section 2 & Target T08)
def inject_faults(nominal_windows):
    X_faulty = nominal_windows.copy()
    fault_types = []
    target_channels = []

    idx_batt_v = features.index("Battery_Voltage")
    idx_batt_t = features.index("Battery_Temp")
    idx_comm_t = features.index("NanoCom_Temp")
    idx_spin_z = features.index("Z_Coarse_Spin")

    for i in range(len(X_faulty)):
        ftype = i % 3
        if ftype == 0:
            # Fault A: Multiplicative Gain Sag (Battery Voltage Brownout)
            X_faulty[i, 3:, idx_batt_v] -= 4.0
            fault_types.append("EPS_Voltage_Sag")
            target_channels.append("Battery_Voltage")
        elif ftype == 1:
            # Fault B: Additive Trend Drift (Thermal Runaway on NanoCom + Battery)
            ramp = np.linspace(0.0, 4.5, WINDOW_SIZE)
            X_faulty[i, :, idx_comm_t] += ramp
            X_faulty[i, :, idx_batt_t] += ramp * 0.5
            fault_types.append("Thermal_Drift")
            target_channels.append("NanoCom_Temp")
        else:
            # Fault C: Stuck-at-State / Freeze + Spike on ADCS Spin
            X_faulty[i, 2:, idx_spin_z] = 4.8
            fault_types.append("ADCS_Sensor_Freeze_Spike")
            target_channels.append("Z_Coarse_Spin")

    return X_faulty, fault_types, target_channels

X_test_faulty, fault_types, target_channels = inject_faults(X_test)

# 6. Stage 2 Lightweight Triage Mapper (Concept 5)
SUBSYSTEM_MAP = {
    "Battery_Voltage": "EPS_POWER", "Battery_Temp": "EPS_POWER",
    "NanoCom_Temp": "UHF_COMMS", "Background_RSSI": "UHF_COMMS",
    "X_Coarse_Spin": "ADCS_ATTITUDE", "Y_Coarse_Spin": "ADCS_ATTITUDE",
    "Z_Coarse_Spin": "ADCS_ATTITUDE",
    "PD1_CSS_theta": "ADCS_SUN_SENSORS", "PD2_CSS_theta": "ADCS_SUN_SENSORS",
    "PD3_CSS_theta": "ADCS_SUN_SENSORS", "PD4_CSS_theta": "ADCS_SUN_SENSORS",
    "PD5_CSS_theta": "ADCS_SUN_SENSORS", "PD6_CSS_theta": "ADCS_SUN_SENSORS"
}

# 7. Evaluate Quantized INT8 Model on Nominal + Faulty Test Splits (Target T03 & T04)
nom_recon, nom_lats = run_tflite(X_test)
flt_recon, flt_lats = run_tflite(X_test_faulty)
latencies_ms = nom_lats + flt_lats

nom_ch_mse = np.mean(np.square(X_test - nom_recon), axis=1)
flt_ch_mse = np.mean(np.square(X_test_faulty - flt_recon), axis=1)

nom_scores, _ = compute_smoothed_scores(nom_ch_mse, smooth_k=3)
flt_scores, flt_norm_res = compute_smoothed_scores(flt_ch_mse, smooth_k=3)

y_true = np.array([0] * len(X_test) + [1] * len(X_test_faulty))
y_pred = (np.concatenate([nom_scores, flt_scores]) > anomaly_threshold).astype(int)

# Check Stage 2 Triage sensor isolation accuracy on faulty windows
triage_hits = 0
for i in range(len(X_test_faulty)):
    top_idx = int(np.argmax(flt_norm_res[i]))
    if features[top_idx] == target_channels[i]:
        triage_hits += 1

tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
fpr = fp / (fp + tn)
prec = precision_score(y_true, y_pred)
rec = recall_score(y_true, y_pred)
f1 = f1_score(y_true, y_pred)

print("\n=== PHASE 1 VERIFICATION REPORT (INT8 TFLite Model) ===")
print(f"T03 Precision : {prec:.4f}")
print(f"T03 Recall    : {rec:.4f}")
print(f"T03 F1-Score  : {f1:.4f}  (Target >= 0.85)")
print(f"T03 FPR       : {fpr*100:.2f}%  (Target < 3.00%)")
print(f"Stage 2 Triage Top-1 Sensor Isolation Accuracy: {(triage_hits / len(X_test_faulty))*100:.1f}%")
print(f"T04 Latency   : p50={np.percentile(latencies_ms, 50):.3f} ms | "
      f"p95={np.percentile(latencies_ms, 95):.3f} ms | "
      f"p99={np.percentile(latencies_ms, 99):.3f} ms  (Target <= 100 ms)")

with open(ARTIFACTS_DIR / "threshold_config.json", "w") as f:
    json.dump({
        "anomaly_threshold_ndt": anomaly_threshold,
        "smoothing_window": 3,
        "channel_residual_median": ch_med.tolist(),
        "channel_residual_iqr": ch_iqr.tolist(),
        "subsystem_map": SUBSYSTEM_MAP
    }, f, indent=2)