# Physical Sensor Fault Injection and Recovery

## 1. Overview

This implementation provides software-controlled fault injection
and recovery for the actual sensors connected to the Raspberry Pi.

Instead of modifying sensor readings inside Python, the fault
injection scripts modify the physical sensor configuration registers
through the I2C bus.

The original register values are saved before injection and restored
during recovery. No physical intervention or sensor replacement
is required for the implemented scenarios.

## 2. Supported Hardware

| Sensor | I2C Address | Function |
|---|---|---|
| INA219 | 0x40 | Voltage and current monitoring |
| MPU6050 | 0x68 | Motion and acceleration monitoring |
| BMP280 | 0x77 | Temperature and pressure monitoring |

All three sensors communicate through I2C bus 1.

## 3. Supported Physical Faults

| Sensor | Fault ID | Expected Effect |
|---|---|---|
| INA219 | power_down | Stops new measurement conversions |
| INA219 | adc_disabled | Disables ADC conversions |
| INA219 | calibration | Incorrect current measurements |
| INA219 | zero_calibration | Zero calculated current |
| MPU6050 | sleep | Stops normal sensor operation |
| MPU6050 | accel_disabled | Disables all accelerometer axes |
| MPU6050 | x_disabled | Disables X-axis acceleration |
| MPU6050 | y_disabled | Disables Y-axis acceleration |
| MPU6050 | z_disabled | Disables Z-axis acceleration |
| BMP280 | sleep | Stops new measurement conversions |
| BMP280 | temp_disabled | Disables temperature conversion |
| BMP280 | pressure_disabled | Disables pressure conversion |

Each of the 12 faults has a corresponding injection and
recovery script, giving a total of 24 executable Python scripts.

## 4. Implementation Structure

```text
flight/
  fault_injection/
    hardware.py
    fault_state.py
    injector.py
    mqtt_listener.py
  recovery/
    recovery_manager.py

scripts/faults/
  ina219/
  mpu6050/
  bmp280/
  fault_status.py

simulator/
  hardware_faults.py
  mqtt.py
  cli.py

tests/
  test_sensor_fault_injection.py
  test_sensor_fault_recovery.py
  test_sensor_fault_integration.py
```

## 5. Dependencies

From the Raspberry Pi repository directory:

```bash
.venv/bin/python -m pip install -r simulator/requirements.txt
.venv/bin/python -m pip install smbus2==0.4.3
```

The Raspberry Pi must have access to /dev/i2c-1.

The existing sensor monitoring script, test.py, requires
its original sensor libraries.

## 6. Manual Fault Injection and Recovery

Coordinate with other team members before using the shared Pi.

Open two Raspberry Pi terminals.

Terminal 1: Navigate to the repository.

```bash
cd ~/Self-healing_Satellite-integration
```

Terminal 2: Start the existing sensor monitoring script.

```bash
cd ~
python3 -u test.py
```

Keep the monitoring script running during injection and recovery.

For example, disable the MPU6050 Y-axis from Terminal 1:

```bash
.venv/bin/python scripts/faults/mpu6050/inject_y_disabled.py
```

Observe the actual Y-axis measurement in Terminal 2.

Check recorded active faults:

```bash
.venv/bin/python scripts/faults/fault_status.py
```

Recover the sensor:

```bash
.venv/bin/python scripts/faults/mpu6050/recover_y_disabled.py
```

Verify that normal readings return and the recorded fault
has been cleared.

The same naming convention applies to every supported fault:

```text
scripts/faults/<sensor>/inject_<fault_id>.py
scripts/faults/<sensor>/recover_<fault_id>.py
```

## 7. Register Backup and Recovery

Before applying a fault, the original register configuration
is saved locally under:

```text
~/.capstone/sensor_faults/
```

The implementation provides:

- Original register backup before modification.
- Hardware register read-back verification.
- One recorded active fault per physical sensor.
- Concurrent access protection for fault-management operations.
- Rollback attempts if injection fails.
- Backup preservation if recovery or rollback fails.
- Backup removal only after verified recovery.

Physical faults do not automatically expire when the
simulated telemetry fault expires.

They remain active until recovery, unless another process
reconfigures or resets the sensor.

Do not delete saved fault states before recovery.

## 8. Automatic Fault Injection over MQTT

The simulator can send authenticated physical fault requests
to the Raspberry Pi.

```text
Laptop Simulator
       |
       | MQTT fault request
       v
Raspberry Pi Fault Listener
       |
       | I2C register modification
       v
Physical Sensor
       |
       v
Actual Sensor Measurements
```

MQTT topic:

```text
satellite/flight/hardware-fault/request
```

Requests use HMAC-SHA256 authentication, unique command IDs,
expiry timestamps, and an allowlist of supported faults.

Duplicate, expired, invalidly signed and retained requests
are rejected or ignored by the receiver.

Hardware injection is disabled by default and requires
explicit activation.

### Start the Raspberry Pi listener

Use the same temporary authentication key on the laptop and Pi.
Generate a fresh random key of at least 32 characters.

Set CAPSTONE_FAULT_KEY privately in both terminal environments.
Never commit or share the actual key in logs.

On the Raspberry Pi:

```bash
.venv/bin/python -u \
-m flight.fault_injection.mqtt_listener \
--mqtt-host 127.0.0.1 \
--mqtt-port 1883 \
--enable-hardware-faults
```

Wait for:

```text
[READY] Hardware fault receiver subscribed
```

Keep test.py running in another Pi terminal.

### Mode A: Automatic hardware testing

Run from the laptop repository directory:

```bash
.venv/bin/python -u -m simulator \
--uhf-csv docs/uhf_telemetry.csv \
--mqtt-host <PI_BROKER_IP> \
--mqtt-port 1883 \
--frames 10 \
--rate-hz 1 \
--seed 42 \
--hardware-faults \
--hardware-fault-tick 5
```

This selects a supported physical fault and sends the
request at frame 5.

In the tested implementation, seed 42 selects
mpu6050/y_disabled.

### Mode B: Matched simulated fault

On macOS/Linux:

```bash
export SIMULATOR_OUTPUT_PROFILE=model13
export SIMULATOR_DEMO_FAULT=0

.venv/bin/python -u -m simulator \
--uhf-csv docs/uhf_telemetry.csv \
--mqtt-host <PI_BROKER_IP> \
--mqtt-port 1883 \
--frames 100 \
--rate-hz 1 \
--seed 2 \
--match-hardware-faults
```

In the tested implementation, seed 2 produces a stuck
Battery_Voltage fault at frame 59.

The simulator automatically requests the corresponding
INA219 power-down fault.

Replace <PI_BROKER_IP> with the reachable Pi LAN or
Tailscale IP address.

Windows users can use .venv\Scripts\python.exe and set
environment variables using PowerShell.

### Supported Matching Rules

| Simulated Signal | Simulated Fault | Physical Fault |
|---|---|---|
| Battery_Voltage | stuck | INA219 power_down |
| Battery_Temp | stuck | BMP280 sleep |
| NanoCom_Temp | stuck | BMP280 sleep |
| temperature_c | stuck | BMP280 sleep |
| pressure_hpa | stuck | BMP280 sleep |

These are representative hardware equivalents.

The BMP280 represents a temperature measurement device,
not an actual satellite battery or communication subsystem.

Unsupported simulated faults remain simulation-only.

Matched mode sends at most one request per physical
sensor during each simulator run.

## 9. Recovery API for FSM and Ground Agent

The recovery manager can be imported directly by other
Python components running on the Raspberry Pi.

Recover a specific fault:

```python
from flight.recovery.recovery_manager import recover_fault

result = recover_fault("mpu6050", "y_disabled")
print(result)
```

Recover the recorded fault on a particular sensor:

```python
result = recover_fault("ina219")
```

Recover all recorded physical faults:

```python
from flight.recovery.recovery_manager import recover_all

for result in recover_all():
    print(result)
```

The FSM or ground agent should check returned recovery
statuses and verify the sensor measurements before
declaring the fault resolved.

These functions must execute on the Raspberry Pi because
they require access to the physical I2C hardware.

## 10. Automated Testing

Run from the repository root:

```bash
python3 -m unittest discover \
-s tests \
-p "test_sensor_fault_*.py" \
-v
```

The automated tests mock hardware and MQTT operations.
No physical sensors or MQTT broker are required.

| Test Suite | Tests Passed |
|---|---:|
| Physical fault injection | 7/7 |
| Physical fault recovery | 9/9 |
| Simulator and MQTT integration | 13/13 |
| Total | 29/29 |

The integration tests also verify all 12 supported fault
messages, matching rules, duplicate requests, invalid
signatures, expired requests and normal simulator operation.

## 11. Physical Hardware Test Results

Testing completed on November 27, 2026.

| Sensor | Register | Original | Fault Value | Restored |
|---|---|---|---|---|
| INA219 | 0x00 | 0x219F | 0x2198 | 0x219F |
| INA219 | 0x00 | 0x219F | 0x219C | 0x219F |
| INA219 | 0x05 | 0x8332 | 0x20CC | 0x8332 |
| INA219 | 0x05 | 0x8332 | 0x0000 | 0x8332 |
| MPU6050 | 0x6B | 0x00 | 0x40 | 0x00 |
| MPU6050 | 0x6C | 0x00 | 0x38 | 0x00 |
| MPU6050 | 0x6C | 0x00 | 0x20 | 0x00 |
| MPU6050 | 0x6C | 0x00 | 0x10 | 0x00 |
| MPU6050 | 0x6C | 0x00 | 0x08 | 0x00 |
| BMP280 | 0xF4 | 0xB7 | 0xB4 | 0xB7 |
| BMP280 | 0xF4 | 0xB7 | 0x17 | 0xB7 |
| BMP280 | 0xF4 | 0xB7 | 0xA3 | 0xB7 |

All 12 physical fault injections and recoveries passed.

Measurement readings or conversion-status flags were
used to confirm the physical effects.

The register values above are observed test values.
The recovery implementation restores the actual saved
configuration rather than relying on hardcoded defaults.

### Live Integration Test 1

Automatic simulator-triggered MPU6050 Y-axis fault:

- Simulator generated a hardware fault request.
- Raspberry Pi received and authenticated the request.
- MPU6050 register 0x6C changed from 0x00 to 0x10.
- Actual Y-axis readings became zero.
- Recovery restored register 0x6C to 0x00.
- Normal readings returned without restarting test.py.

Result: PASS.

### Live Integration Test 2

Matched simulated Battery_Voltage stuck fault:

- Simulator generated a stuck voltage fault at frame 59.
- Corresponding INA219 power-down request was published.
- INA219 register 0x00 changed to 0x2198.
- Physical conversion-ready status became false.
- Voltage and current readings stopped updating.
- Recovery restored register 0x00 to 0x219F.
- Conversion-ready status returned to true.
- Normal measurements resumed.

Result: PASS.

### Final Hardware State

All five relevant configuration registers were restored
to their original values.

No recorded active faults remained.

## 12. Cleanup

Stop the simulator and MQTT fault listener before cleanup.

Recover every recorded physical fault on the Pi:

```bash
.venv/bin/python - <<'PY'
from flight.recovery.recovery_manager import recover_all

for result in recover_all():
    print(result)
    if result["status"] == "RECOVERY_FAILED":
        raise RuntimeError("Recovery failed")
PY
```

Check the recorded fault status:

```bash
.venv/bin/python scripts/faults/fault_status.py
```

Confirm that the sensor readings are normal.

Remove temporary authentication keys from terminal
environments after testing.

On macOS/Linux:

```bash
unset CAPSTONE_FAULT_KEY
```

## 13. Limitations and Future Integration

- Spike, noise, drift and actual voltage sag remain
  simulation-only faults.
- The existing I2C permission scripts were not physically
  tested because sudo access was unavailable.
- I2C permission changes may not affect already-open
  device handles.
- Simulator-side fault persistence is not modified.
- Autonomous FSM and ground-agent recovery orchestration
  is not implemented here.
- MQTT publication confirmation does not independently
  confirm physical execution.
- Duplicate command tracking is in memory.
- The fault listener is a controlled testing component,
  not a production spacecraft security system.
- The existing one-command demo launcher is unchanged.
- Virtual hardware sensors are not implemented.

## 14. Implementation Summary

The implementation delivers 12 software-controlled
physical sensor fault scenarios, 12 corresponding recovery
scripts, register backups, MQTT-based automatic triggering,
and callable recovery functions for future FSM integration.

All 29 automated tests, 12 physical injection/recovery
scenarios and both live MQTT integration tests passed.
