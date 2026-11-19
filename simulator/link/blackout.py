"""A deterministic, simulated *ground-link* outage. Does not disable Pi Wi-Fi."""
from dataclasses import dataclass


@dataclass(frozen=True)
class BlackoutWindow:
    after_seconds: float = 20.0
    duration_seconds: float = 60.0

    def __post_init__(self):
        if self.after_seconds < 0 or self.duration_seconds <= 0:
            raise ValueError("blackout delay must be >= 0 and duration must be > 0")

    def offline(self, elapsed_seconds: float) -> bool:
        return self.after_seconds <= elapsed_seconds < self.after_seconds + self.duration_seconds

    def finished(self, elapsed_seconds: float) -> bool:
        return elapsed_seconds >= self.after_seconds + self.duration_seconds
