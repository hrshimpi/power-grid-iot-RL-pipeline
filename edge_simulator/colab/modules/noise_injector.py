"""
noise_injector.py — Grid voltage schema
Adds sensor noise and drift to grid readings.

Fields affected:
  voltage_v:     Gaussian noise ± sigma_v
  current_a:     Gaussian noise ± sigma_i
  frequency_hz:  Gaussian noise ± sigma_f
  temperature_c: Gaussian noise ± sigma_t
  power_factor:  Gaussian noise ± sigma_pf
"""

import random
import copy
from dataclasses import dataclass


@dataclass
class NoiseConfig:
    enabled:        bool  = False
    # Gaussian noise sigmas per field
    sigma_v:        float = 1.5    # Volts
    sigma_i:        float = 2.0    # Amperes
    sigma_f:        float = 0.01   # Hz
    sigma_t:        float = 0.5    # Celsius
    sigma_pf:       float = 0.005  # power factor
    # drift — accumulates per reading
    drift_enabled:  bool  = False
    drift_v:        float = 0.02   # V per reading
    drift_i:        float = 0.01   # A per reading
    # corruption — sudden wrong value
    corruption_prob: float = 0.0
    v_corrupt_range: tuple = (300, 500)
    i_corrupt_range: tuple = (0, 250)


class NoiseInjector:

    def __init__(self, config: NoiseConfig = None):
        self.cfg = config or NoiseConfig()
        self._drift_v = 0.0
        self._drift_i = 0.0

    def inject(self, reading: dict) -> dict:
        if not self.cfg.enabled:
            return reading

        out = copy.deepcopy(reading)

        # corruption — sudden bad value
        if random.random() < self.cfg.corruption_prob:
            field = random.choice(["voltage_v", "current_a"])
            if field == "voltage_v":
                out["voltage_v"] = round(random.uniform(*self.cfg.v_corrupt_range), 2)
            else:
                out["current_a"] = round(random.uniform(*self.cfg.i_corrupt_range), 2)
            out["_corrupted"] = True
            return out

        # gaussian noise
        out["voltage_v"]     = round(out["voltage_v"]     + random.gauss(0, self.cfg.sigma_v),  2)
        out["current_a"]     = round(out["current_a"]     + random.gauss(0, self.cfg.sigma_i),  2)
        out["frequency_hz"]  = round(out["frequency_hz"]  + random.gauss(0, self.cfg.sigma_f),  3)
        out["temperature_c"] = round(out["temperature_c"] + random.gauss(0, self.cfg.sigma_t),  1)
        out["power_factor"]  = round(max(0, min(1, out["power_factor"] + random.gauss(0, self.cfg.sigma_pf))), 3)

        # drift
        if self.cfg.drift_enabled:
            self._drift_v += self.cfg.drift_v
            self._drift_i += self.cfg.drift_i
            out["voltage_v"] = round(out["voltage_v"] + self._drift_v, 2)
            out["current_a"] = round(out["current_a"] + self._drift_i, 2)

        out["_corrupted"] = False
        return out

    def reset_drift(self):
        self._drift_v = 0.0
        self._drift_i = 0.0
