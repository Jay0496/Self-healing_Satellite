import random
from dataclasses import replace

class ContinuousFaultInjector:
    def __init__(self, probability=0.08):
        # 8% chance to trigger a new fault on ANY given tick
        self.probability = probability 
        self.active_faults = {}
        self.safe_sensors = ['temperature_c', 'cpu_usage', 'memory_usage', 'pressure_hpa']

    def inject_records(self, records):
        # 1. INDEPENDENT DICE ROLL
        if random.random() < self.probability:
            self._start_new_fault()

        new_records = []
        
        # 2. Apply faults by creating new replacement records
        for record in records:
            if record.signal in self.active_faults:
                fault = self.active_faults[record.signal]
                new_value = record.value
                
                # Calculate the math on the temporary new_value variable
                if fault['type'] == 'spike':
                    new_value += random.uniform(20.0, 50.0)
                    fault['duration'] = 0
                elif fault['type'] == 'noise':
                    new_value += random.uniform(-5.0, 5.0)
                    fault['duration'] -= 1
                elif fault['type'] == 'drift':
                    fault['current_drift'] += fault['drift_rate']
                    new_value += fault['current_drift']
                    fault['duration'] -= 1
                elif fault['type'] == 'stuck':
                    new_value = fault['stuck_value']
                    fault['duration'] -= 1
                    
                # Create a new record copy with the injected value and add it to our new list
                new_records.append(replace(record, value=new_value))
            else:
                # No fault active for this sensor; keep the original record
                new_records.append(record)

        # 3. CLEANUP: Find and remove any faults that have expired
        expired_sensors = [sensor for sensor, f in self.active_faults.items() if f['duration'] <= 0]
        for sensor in expired_sensors:
            print(f"[FAULT ALERT] {self.active_faults[sensor]['type']} resolved on {sensor}. Returning to normal.")
            del self.active_faults[sensor]

        # Return the entirely new list of records
        return new_records

    def _start_new_fault(self):
        available_sensors = [s for s in self.safe_sensors if s not in self.active_faults]
        if not available_sensors:
            return 
            
        target = random.choice(available_sensors)
        fault_type = random.choice(['spike', 'noise', 'drift', 'stuck'])
        
        duration = 1 if fault_type == 'spike' else random.randint(5, 120)
        
        fault_data = {
            'type': fault_type,
            'duration': duration,
        }
        
        if fault_type == 'drift':
            fault_data['drift_rate'] = random.uniform(-1.5, 1.5)
            fault_data['current_drift'] = 0.0
        elif fault_type == 'stuck':
            fault_data['stuck_value'] = random.uniform(10.0, 40.0)
            
        self.active_faults[target] = fault_data
        print(f"[FAULT ALERT] Started {fault_type} on {target} for {duration} ticks")