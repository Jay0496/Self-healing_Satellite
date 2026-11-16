import random
from shared.protocol import TelemetryRecord

class SpikeFaultTransform:
    """Injects a random multiplier spike, maintaining P = V * I validation."""
    
    def __init__(self, target_signal: str, max_multiplier: float = 2.0):
        self.target_signal = target_signal
        self.max_multiplier = max_multiplier
        self._sample_multipliers = {}  # Tracks the random multiplier for a given sample timeframe

    def apply(self, record: TelemetryRecord) -> TelemetryRecord:
        # Prevent memory leaks by occasionally clearing the dictionary
        if len(self._sample_multipliers) > 100:
            self._sample_multipliers.clear()

        # Generate and store one consistent random multiplier per sample timeframe
        if record.sample_id not in self._sample_multipliers:
            self._sample_multipliers[record.sample_id] = random.uniform(1.0, self.max_multiplier)
        
        multiplier = self._sample_multipliers[record.sample_id]

        # Check if we should mutate this record.
        # We mutate the target signal AND we mutate power_mw if it depends on voltage/current.
        is_target = (record.signal == self.target_signal)
        is_dependent_power = (record.signal == "power_mw" and self.target_signal in ["bus_voltage_v", "current_ma"])

        if not (is_target or is_dependent_power):
            return record

        # Apply the exact same multiplier so Power = Voltage * Current remains mathematically true
        fault_value = record.value * multiplier
        
        return TelemetryRecord(
            run_id=record.run_id,
            sample_id=record.sample_id,
            sequence=record.sequence,
            sensor_id=record.sensor_id,
            signal=record.signal,
            value=round(float(fault_value), 6),
            unit=record.unit,
            measurement_time=record.measurement_time,
            publish_time=record.publish_time,
            source=record.source,
            transform_id=f"fault_spike_{self.target_signal}",
            provenance="fault_injected"
        )