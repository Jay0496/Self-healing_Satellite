import json
from collections import deque
from pathlib import Path
import numpy as np

try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        import tensorflow as tf
        Interpreter = tf.lite.Interpreter


class EdgeAnomalyDetector:
    """
    Phase 1 Dual-Stage Edge Anomaly Detector (Concept 2 + Concept 5).
    Stage 1: Quantized INT8 1D-CNN Autoencoder + Nonparametric Dynamic Thresholding (NDT).
    Stage 2: Per-channel residual isolation & subsystem triage for the Deterministic FSM.
    """

    def __init__(self, artifacts_dir=None):
        base = Path(artifacts_dir) if artifacts_dir else Path(__file__).resolve().parent / "artifacts"

        with open(base / "scaler_params.json") as f:
            self.scaler_cfg = json.load(f)
        with open(base / "threshold_config.json") as f:
            self.thresh_cfg = json.load(f)

        self.features = self.scaler_cfg["features"]
        self.window_size = self.scaler_cfg["window_size"]
        self.center = np.array(self.scaler_cfg["center"], dtype=np.float32)
        self.scale = np.array(self.scaler_cfg["scale"], dtype=np.float32)
        self.medians = np.array(self.scaler_cfg["impute_medians"], dtype=np.float32)
        self.clip_min, self.clip_max = self.scaler_cfg["clip_range"]

        self.threshold = float(self.thresh_cfg["anomaly_threshold_ndt"])
        self.smooth_k = int(self.thresh_cfg["smoothing_window"])
        self.ch_med = np.array(self.thresh_cfg["channel_residual_median"], dtype=np.float32)
        self.ch_iqr = np.array(self.thresh_cfg["channel_residual_iqr"], dtype=np.float32)
        self.subsystem_map = self.thresh_cfg["subsystem_map"]

        self.window_buffer = deque(maxlen=self.window_size)
        self.score_history = deque(maxlen=self.smooth_k)

        self.interpreter = Interpreter(model_path=str(base / "model_int8.tflite"))
        self.interpreter.allocate_tensors()
        self.in_idx = self.interpreter.get_input_details()[0]["index"]
        self.out_idx = self.interpreter.get_output_details()[0]["index"]

    def _preprocess_record(self, telemetry_dict: dict) -> np.ndarray:
        raw = np.array(
            [telemetry_dict.get(feat, np.nan) for feat in self.features],
            dtype=np.float32
        )
        nan_mask = np.isnan(raw)
        raw[nan_mask] = self.medians[nan_mask]
        scaled = (raw - self.center) / self.scale
        return np.clip(scaled, self.clip_min, self.clip_max)

    def push_and_evaluate(self, telemetry_dict: dict) -> dict:
        """
        Ingests one telemetry dictionary from MQTT/I2C.
        Returns status='WARMING_UP' until window_size (10) steps are buffered,
        then returns Stage 1 anomaly flag + Stage 2 subsystem triage context.
        """
        vec = self._preprocess_record(telemetry_dict)
        self.window_buffer.append(vec)

        if len(self.window_buffer) < self.window_size:
            return {
                "status": "WARMING_UP",
                "buffered": len(self.window_buffer),
                "required": self.window_size,
                "is_anomaly": False
            }

        window = np.expand_dims(np.array(self.window_buffer, dtype=np.float32), axis=0)
        self.interpreter.set_tensor(self.in_idx, window)
        self.interpreter.invoke()
        recon = self.interpreter.get_tensor(self.out_idx)

        # Stage 1: NDT Smoothed Anomaly Score
        ch_mse = np.mean(np.square(window[0] - recon[0]), axis=0)
        norm_residuals = (ch_mse - self.ch_med) / self.ch_iqr
        raw_score = float(np.max(norm_residuals))
        self.score_history.append(raw_score)
        smoothed_score = float(np.mean(self.score_history))

        is_anomaly = smoothed_score > self.threshold

        # Stage 2: Lightweight Triage (Pinpoint failing sensor & subsystem for FSM)
        top_idx = int(np.argmax(norm_residuals))
        suspect_sensor = self.features[top_idx]
        suspect_subsystem = self.subsystem_map.get(suspect_sensor, "GENERAL_BUS")

        return {
            "status": "ANOMALY" if is_anomaly else "NOMINAL",
            "is_anomaly": is_anomaly,
            "anomaly_score": round(smoothed_score, 4),
            "threshold": round(self.threshold, 4),
            "suspect_sensor": suspect_sensor if is_anomaly else None,
            "suspect_subsystem": suspect_subsystem if is_anomaly else None,
            "top_channel_z": round(float(norm_residuals[top_idx]), 4)
        }


if __name__ == "__main__":
    detector = EdgeAnomalyDetector()
    nominal_sample = {
        "X_Coarse_Spin": 0.005, "Y_Coarse_Spin": 0.001, "Z_Coarse_Spin": -0.002,
        "PD1_CSS_theta": 0.1, "PD2_CSS_theta": 0.03, "PD3_CSS_theta": 0.06,
        "PD4_CSS_theta": 0.03, "PD5_CSS_theta": 0.08, "PD6_CSS_theta": 0.04,
        "Battery_Temp": 300.0, "Battery_Voltage": 7890.0,
        "Background_RSSI": -112.0, "NanoCom_Temp": 344.0
    }

    for step in range(12):
        res = detector.push_and_evaluate(nominal_sample)
    print("Nominal check:", res)

    fault_sample = dict(nominal_sample, Battery_Voltage=4200.0)
    for step in range(8):
        res = detector.push_and_evaluate(fault_sample)
    print("Fault triage check:", res)