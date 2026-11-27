"""REQ-HW-01/02/03 monitoring: sampling, limit breaches, Pi temperature and PMIC power parsing."""
import tempfile
import unittest
from pathlib import Path

from flight.monitoring.resource_monitor import (ResourceLimits, ResourceMonitor, parse_pmic_adc,
                                                read_temperature_c)

PI5_PMIC_SAMPLE = """\
     3V7_WL_SW_A current(0)=0.10000000A
       3V3_SYS_A current(1)=0.20000000A
      1V1_SYS_A current(5)=0.50000000A
     3V7_WL_SW_V volt(8)=3.70000000V
       3V3_SYS_V volt(9)=3.30000000V
      1V1_SYS_V volt(13)=1.10000000V
      EXT5V_V volt(24)=5.10000000V
"""


class TestResourceMonitor(unittest.TestCase):
    def test_sample_reports_agent_footprint(self):
        sample = ResourceMonitor().sample()
        for key in ('cpu_percent', 'agent_ram_mb', 'agent_processes', 'cpu_temp_c', 'power_w',
                    'breaches', 'limits'):
            self.assertIn(key, sample)
        self.assertGreater(sample['agent_ram_mb'], 1)               # at least this test process
        self.assertLess(sample['agent_ram_mb'], 2048)               # REQ-HW-02 under nominal load
        self.assertEqual(sample['limits']['max_temp_c'], 70.0)

    def test_breach_detected_when_limit_exceeded(self):
        monitor = ResourceMonitor(ResourceLimits(max_agent_ram_mb=0.001))
        self.assertIn('RAM>0.001', monitor.sample()['breaches'])

    def test_limit_logic_for_temperature_and_power(self):
        monitor = ResourceMonitor()
        self.assertEqual(monitor.breaches({'cpu_temp_c': 71.0, 'power_w': 9.0}), ['TEMP>70'])
        self.assertEqual(monitor.breaches({'cpu_temp_c': None, 'power_w': 10.5}), ['POWER>10'])
        self.assertEqual(monitor.breaches({'cpu_temp_c': 55.0, 'agent_ram_mb': 300}), [])

    def test_pi5_pmic_power_estimate(self):
        # 0.1*3.7 + 0.2*3.3 + 0.5*1.1 = 0.37 + 0.66 + 0.55 = 1.58 W (EXT5V has no current rail)
        self.assertAlmostEqual(parse_pmic_adc(PI5_PMIC_SAMPLE), 1.58, places=3)
        self.assertIsNone(parse_pmic_adc('error=1 error_msg="Command not registered"'))

    def test_pi_thermal_zone_read(self):
        with tempfile.TemporaryDirectory() as td:
            zone = Path(td) / 'temp'
            zone.write_text('48312\n')
            self.assertEqual(read_temperature_c(zone), 48.3)


if __name__ == '__main__':
    unittest.main()
