import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.preprocessing import RobustScaler

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
FIGURES_DIR = BASE_DIR / "figures"

ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

# 1. Load & Sort Chronologically
df = pd.read_csv(DATA_DIR / "uhf_telemetry.csv")
df["Timestamp"] = pd.to_datetime(df["Timestamp"])
df = df.sort_values("Timestamp").reset_index(drop=True)

# Plot 1: Raw Packet Completeness (Before Cleaning)
plt.figure(figsize=(12, 5))
completeness = (df.drop(columns=["Timestamp"]).notna().mean() * 100).sort_values()
completeness.plot(kind="barh", color="#2b5c8f")
plt.xlabel("Non-Null Packet Coverage (%)")
plt.title("Raw OPS-SAT-1 Telemetry Completeness Across 16,500 Rows")
plt.tight_layout()
plt.savefig(FIGURES_DIR / "01_raw_completeness.png", dpi=300)
plt.close()

# 2. Segment Into Contiguous Ground Passes (New pass if gap > 60s)
df["gap_sec"] = df["Timestamp"].diff().dt.total_seconds().fillna(0)
df["pass_id"] = (df["gap_sec"] > 60).cumsum()

# Select the 13 continuous analog telemetry channels across EPS, Thermal, Comms & ADCS
active_cols = [
    "X_Coarse_Spin", "Y_Coarse_Spin", "Z_Coarse_Spin",
    "PD1_CSS_theta", "PD2_CSS_theta", "PD3_CSS_theta",
    "PD4_CSS_theta", "PD5_CSS_theta", "PD6_CSS_theta",
    "Battery_Temp", "Battery_Voltage",
    "Background_RSSI", "NanoCom_Temp"
]

valid_passes = [
    pid for pid, g in df.groupby("pass_id")
    if g["Battery_Voltage"].notna().sum() >= 3 and g["NanoCom_Temp"].notna().sum() >= 3
]
df_multi = df[df["pass_id"].isin(valid_passes)].copy()

# 3. Resample Each Pass to a Unified 5-Second Cadence (No Bridging Across Blackouts)
cleaned_passes = []
for pid, group in df_multi.groupby("pass_id"):
    g = group[["Timestamp"] + active_cols].set_index("Timestamp")
    g = g.groupby(level=0).mean().resample("5s").mean()
    g = g.interpolate(method="linear").bfill().ffill()
    g["pass_id"] = pid
    if len(g) >= 10:
        cleaned_passes.append(g)

df_clean = pd.concat(cleaned_passes)

# 4. Stratified Pass-Level Split (Balances orbital sun/eclipse modes without window leakage)
unique_pids = df_clean["pass_id"].unique()
val_pids = unique_pids[4::7]
test_pids = unique_pids[6::7]
train_pids = [p for p in unique_pids if p not in set(val_pids) and p not in set(test_pids)]

train_df = df_clean[df_clean["pass_id"].isin(train_pids)].copy()
val_df = df_clean[df_clean["pass_id"].isin(val_pids)].copy()
test_df = df_clean[df_clean["pass_id"].isin(test_pids)].copy()

# Impute any pass-wide unlogged channels using training medians (guarantees 0 NaNs)
train_medians = train_df[active_cols].median()
train_df[active_cols] = train_df[active_cols].fillna(train_medians)
val_df[active_cols] = val_df[active_cols].fillna(train_medians)
test_df[active_cols] = test_df[active_cols].fillna(train_medians)

# Plot 2: Cross-Sensor Correlation Heatmap
plt.figure(figsize=(11, 9))
corr = train_df[active_cols].corr()
sns.heatmap(corr, cmap="coolwarm", center=0, vmin=-1, vmax=1, annot=True, fmt=".2f", annot_kws={"size": 8})
plt.title("Cross-Sensor Telemetry Correlation Matrix (Synchronized 5s Nominal Baseline)")
plt.tight_layout()
plt.savefig(FIGURES_DIR / "02_correlation_heatmap.png", dpi=300)
plt.close()

# Plot 3: Sample Multi-Subsystem Nominal Pass Trajectory
sample_pid = cleaned_passes[0]["pass_id"].iloc[0]
sample_pass = df_clean[df_clean["pass_id"] == sample_pid]
fig, axes = plt.subplots(3, 1, figsize=(11, 7), sharex=True)
axes[0].plot(sample_pass.index, sample_pass["Battery_Voltage"], color="#1f77b4")
axes[0].set_ylabel("Batt Volt (mV)")
axes[1].plot(sample_pass.index, sample_pass["Battery_Temp"] / 10.0, label="Battery (°C)", color="#d62728")
axes[1].plot(sample_pass.index, sample_pass["NanoCom_Temp"] / 10.0, label="NanoCom (°C)", color="#ff7f0e")
axes[1].set_ylabel("Temp (°C)")
axes[1].legend()
axes[2].plot(sample_pass.index, sample_pass["Background_RSSI"], color="#2ca02c")
axes[2].set_ylabel("BG RSSI (dBm)")
axes[0].set_title(f"Synchronized Nominal Telemetry Pass (Pass ID {sample_pid})")
plt.tight_layout()
plt.savefig(FIGURES_DIR / "03_nominal_pass_sample.png", dpi=300)
plt.close()

# 5. Fit RobustScaler ONLY on Training Passes & Export Constants for Raspberry Pi
scaler = RobustScaler()
scaler.fit(train_df[active_cols])

with open(ARTIFACTS_DIR / "scaler_params.json", "w") as f:
    json.dump({
        "features": active_cols,
        "window_size": 10,
        "step_seconds": 5,
        "center": scaler.center_.tolist(),
        "scale": scaler.scale_.tolist(),
        "impute_medians": train_medians.tolist(),
        "clip_range": [-5.0, 5.0]
    }, f, indent=2)

# 6. Build Sliding Windows Per Pass (Never Crossing Pass Boundaries)
def build_pass_windows(sub_df, window_size=10):
    windows = []
    for _, pass_grp in sub_df.groupby("pass_id"):
        scaled = np.clip(scaler.transform(pass_grp[active_cols]), -5.0, 5.0)
        for i in range(len(scaled) - window_size + 1):
            windows.append(scaled[i : i + window_size])
    return np.array(windows, dtype=np.float32)

WINDOW_SIZE = 10
X_train = build_pass_windows(train_df, WINDOW_SIZE)
X_val = build_pass_windows(val_df, WINDOW_SIZE)
X_test = build_pass_windows(test_df, WINDOW_SIZE)

assert not np.isnan(X_train).any(), "NaN found in X_train!"
assert not np.isnan(X_val).any(), "NaN found in X_val!"
assert not np.isnan(X_test).any(), "NaN found in X_test!"

np.savez(DATA_DIR / "processed_windows.npz", X_train=X_train, X_val=X_val, X_test=X_test)
print(f"Verified 0 NaNs -> Train: {X_train.shape}, Val: {X_val.shape}, Test: {X_test.shape}")