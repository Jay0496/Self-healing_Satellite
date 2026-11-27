"""Edge-computer resource monitoring against PR#1 limits (REQ-HW-01/02/03, REQ-FSM-01 CPU).

Cross-platform via psutil (replaces the Linux-only C++ /proc reader). On the Raspberry Pi it
also reads the SoC temperature from the kernel thermal zone and, on a Pi 5, an estimate of
board power from the PMIC (`vcgencmd pmic_read_adc`). Where a value is unavailable (e.g. no
temperature sensor on a laptop, or a Pi 4 without a PMIC ADC) it is reported as ``None``
rather than guessed; power acceptance testing still uses the inline USB meter from PR#1.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil

THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")
_ADC_LINE = re.compile(r"^\s*(\S+?)_(?:[AV])\s+(current|volt)\(\d+\)=([\d.]+)([AV])\s*$")


@dataclass(frozen=True)
class ResourceLimits:
    max_temp_c: float = 70.0        # REQ-HW-01
    max_agent_ram_mb: float = 2048  # REQ-HW-02 (2 GB for monitoring + ML inference)
    max_power_w: float = 10.0       # REQ-HW-03
    max_cpu_percent: float = 80.0   # REQ-FSM-01 "<= 80 % CPU load"


def parse_pmic_adc(text: str) -> float | None:
    """Sum V*I over rails reported by `vcgencmd pmic_read_adc` (Raspberry Pi 5)."""
    currents, volts = {}, {}
    for line in text.splitlines():
        match = _ADC_LINE.match(line)
        if match:
            rail, kind, value, _unit = match.groups()
            (currents if kind == "current" else volts)[rail] = float(value)
    rails = currents.keys() & volts.keys()
    if not rails:
        return None
    return round(sum(currents[r] * volts[r] for r in rails), 3)


def read_temperature_c(thermal_zone: Path = THERMAL_ZONE) -> float | None:
    try:
        return round(int(thermal_zone.read_text().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        pass
    reader = getattr(psutil, "sensors_temperatures", None)
    if reader:
        try:
            for entries in reader().values():
                for entry in entries:
                    if entry.current:
                        return round(float(entry.current), 1)
        except (OSError, RuntimeError):
            pass
    return None


def read_power_w() -> float | None:
    if not shutil.which("vcgencmd"):
        return None
    try:
        out = subprocess.run(["vcgencmd", "pmic_read_adc"], capture_output=True, text=True,
                             timeout=2, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_pmic_adc(out)


class ResourceMonitor:
    """Samples the agent's footprint. ``agent_markers`` select which processes count as the agent."""

    def __init__(self, limits: ResourceLimits | None = None,
                 agent_markers: tuple[str, ...] = ("flight.communications", "flight.anomaly_detection")):
        self.limits = limits or ResourceLimits()
        self.agent_markers = agent_markers
        psutil.cpu_percent(interval=None)  # prime the CPU counter

    def _agent_processes(self) -> list[psutil.Process]:
        found = {os.getpid(): psutil.Process(os.getpid())}
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
            except (psutil.Error, TypeError):
                continue
            if any(marker in cmdline for marker in self.agent_markers):
                found[proc.pid] = proc
        return list(found.values())

    def sample(self) -> dict:
        rss = 0
        processes = self._agent_processes()
        for proc in processes:
            try:
                rss += proc.memory_info().rss
            except psutil.Error:
                continue
        memory = psutil.virtual_memory()
        snapshot = {
            "time": time.time(),
            "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
            "agent_ram_mb": round(rss / 2**20, 1),
            "agent_processes": len(processes),
            "system_ram_used_mb": round((memory.total - memory.available) / 2**20, 1),
            "system_ram_total_mb": round(memory.total / 2**20, 1),
            "cpu_temp_c": read_temperature_c(),
            "power_w": read_power_w(),
        }
        snapshot["breaches"] = self.breaches(snapshot)
        snapshot["limits"] = asdict(self.limits)
        return snapshot

    def breaches(self, snapshot: dict) -> list[str]:
        checks = (("cpu_temp_c", self.limits.max_temp_c, "TEMP"),
                  ("agent_ram_mb", self.limits.max_agent_ram_mb, "RAM"),
                  ("power_w", self.limits.max_power_w, "POWER"),
                  ("cpu_percent", self.limits.max_cpu_percent, "CPU"))
        return [f"{label}>{limit:g}" for key, limit, label in checks
                if snapshot.get(key) is not None and snapshot[key] > limit]
