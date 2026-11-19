# Self-Healing Satellite Simulator - Fault Injection Architecture

This repository contains a software-based fault injection pipeline designed to test a TensorFlow Lite Autoencoder and Finite State Machine (FSM) for an autonomous satellite mission control agent. The system is designed to run on a Raspberry Pi 5.

## Overview
The fault injection architecture bypasses hardware relays and manipulates telemetry entirely at the script level. It intercepts legitimate I2C telemetry from physical sensors (INA219, MPU-6050, BMP280) and programmatically corrupts the data based on predetermined probabilistic profiles before passing it to the mission control pipeline.

## Features
- **Continuous, Multi-Fault Injection:** The system can handle multiple, overlapping faults across different sensors simultaneously.
- **Realistic Fault Profiles:** Models spike, noise, drift, and stuck-at faults based on academic research of physical hardware degradation (e.g., thermal drift, blown fuses, MEMS stiction).
- **Safe Sensor Targeting:** Randomly targets safe sensors (Temperature, Pressure, CPU Usage, Memory Usage) to avoid disrupting strict mathematical validations (like P = V * I for the power monitor).
- **Dynamic Durations:** Faults have highly variable lifespans, ranging from instantaneous spikes to multi-minute drifts or frozen sensors.
- **Non-Destructive Testing:** Validates Machine Learning anomaly detection and FSM recovery logic without physical hardware destruction.

## Architecture
The system consists of two primary components integrated into the existing simulator:

1.  **`simulator/fault_injector.py`**: A middleware state machine (`ContinuousFaultInjector`) that tracks and applies randomized faults over time.
2.  **`simulator/generation.py`**: The core data engine, modified to pass generated telemetry records through the `ContinuousFaultInjector` before packaging them for MQTT publication.

## Getting Started

### Prerequisites
- Raspberry Pi 5 with I2C enabled.
- Python 3 environment.

### Local Testing
You can test the data manipulation logic locally without the physical hardware:
1.  Run the simulator: `python -m simulator.cli`
2.  Monitor the terminal output for `[FAULT ALERT]` messages indicating when faults begin and end.

### Hardware Testing (Raspberry Pi)
To test physical hardware failure recovery, the repository includes bash scripts to break and restore the I2C bus:
- **Break:** `sudo ./scripts/faults/break_sensor.sh` (Revokes I2C permissions to simulate a disconnect).
- **Recover:** `sudo ./scripts/faults/recover_sensor.sh` (Restores permissions).