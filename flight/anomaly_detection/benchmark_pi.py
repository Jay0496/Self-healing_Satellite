import time
import os
import psutil
import numpy as np
from edge_detector import EdgeAnomalyDetector

process = psutil.Process(os.getpid())
ram_before_mb = process.memory_info().rss / (1024 * 1024)

detector = EdgeAnomalyDetector()
ram_after_mb = process.memory_info().rss / (1024 * 1024)

nominal_sample = {
    "X_Coarse_Spin": 0.005, "Y_Coarse_Spin": 0.001, "Z_Coarse_Spin": -0.002,
    "PD1_CSS_theta": 0.1, "PD2_CSS_theta": 0.03, "PD3_CSS_theta": 0.06,
    "PD4_CSS_theta": 0.03, "PD5_CSS_theta": 0.08, "PD6_CSS_theta": 0.04,
    "Battery_Temp": 300.0, "Battery_Voltage": 7890.0,
    "Background_RSSI": -112.0, "NanoCom_Temp": 344.0
}

# Warm up the 10-step rolling buffer
for _ in range(10):
    detector.push_and_evaluate(nominal_sample)

# Benchmark 1,000 live inference + Stage 2 triage cycles on Raspberry Pi ARM CPU
latencies_ms = []
for i in range(1000):
    # Inject a voltage sag halfway through to benchmark both nominal and fault triage paths
    pkt = dict(nominal_sample, Battery_Voltage=4200.0) if i >= 500 else nominal_sample
    t0 = time.perf_counter()
    res = detector.push_and_evaluate(pkt)
    latencies_ms.append((time.perf_counter() - t0) * 1000.0)

print("=== RASPBERRY PI HARDWARE BENCHMARK (TARGET T04) ===")
print(f"Model + Runtime RAM Footprint : {ram_after_mb:.2f} MB (Delta: +{ram_after_mb - ram_before_mb:.2f} MB)")
print(f"p50 Inference + Triage Latency: {np.percentile(latencies_ms, 50):.3f} ms")
print(f"p95 Inference + Triage Latency: {np.percentile(latencies_ms, 95):.3f} ms")
print(f"p99 Inference + Triage Latency: {np.percentile(latencies_ms, 99):.3f} ms  (Target <= 100 ms)")
print(f"Max Inference + Triage Latency: {np.max(latencies_ms):.3f} ms")
print(f"Sample Fault Triage Output    : {res}")