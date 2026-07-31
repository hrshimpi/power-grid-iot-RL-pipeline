"""
Power grid voltage control data generator.

Schema (5 features — matches RL model input):
  voltage_v:      Grid voltage in Volts
  current_a:      Line current in Amperes
  frequency_hz:   Grid frequency in Hertz
  temperature_c:  Transformer temperature in Celsius
  power_factor:   Power factor (0 to 1)

Normal operating ranges (IEC/IEEE standards):
  voltage_v:     400 - 430 V  (nominal 415V)
  current_a:      60 - 100 A  (nominal 82A)
  frequency_hz: 49.9 - 50.1 Hz (nominal 50Hz)
  temperature_c:  30 -  55 C  (nominal 42C)
  power_factor:  0.90 - 0.99  (nominal 0.95)

Fault types and ranges:
  voltage_sag:       V = 340-385  (8-18% below nominal)
  voltage_swell:     V = 440-470  (6-13% above nominal)
  overcurrent:       I = 130-180  (60-120% above nominal)
  frequency_drop:   Hz = 48.0-49.2
  overtemperature:   T = 70-95
  low_power_factor: PF = 0.55-0.78
  multi_fault:       multiple fields simultaneously
"""

import random
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class GridState(Enum):
    NORMAL           = "normal"
    VOLTAGE_SAG      = "voltage_sag"
    VOLTAGE_SWELL    = "voltage_swell"
    OVERCURRENT      = "overcurrent"
    FREQUENCY_DROP   = "frequency_drop"
    OVERTEMPERATURE  = "overtemperature"
    LOW_POWER_FACTOR = "low_power_factor"
    MULTI_FAULT      = "multi_fault"


@dataclass
class GridReading:
    device_id:     str
    timestamp:     str
    voltage_v:     float
    current_a:     float
    frequency_hz:  float
    temperature_c: float
    power_factor:  float
    grid_state:    str

    def to_mqtt_payload(self) -> dict:
        return {
            "device_id":     self.device_id,
            "timestamp":     self.timestamp,
            "voltage_v":     round(self.voltage_v, 2),
            "current_a":     round(self.current_a, 2),
            "frequency_hz":  round(self.frequency_hz, 3),
            "temperature_c": round(self.temperature_c, 1),
            "power_factor":  round(self.power_factor, 3),
        }

    def to_labeled_record(self) -> dict:
        d = self.to_mqtt_payload()
        d["grid_state"] = self.grid_state
        v, hz, i, t, pf = self.voltage_v, self.frequency_hz, self.current_a, self.temperature_c, self.power_factor
        d["label"] = 1 if (400 <= v <= 430 and 49.9 <= hz <= 50.1 and i < 110 and t < 60 and pf > 0.88) else 0
        return d


class GridDataGenerator:

    def __init__(self, device_id="hitachi-substation-01", fault_probability=0.0, seed=None):
        self.device_id = device_id
        self.fault_probability = fault_probability
        self.tick = 0
        self.current_state = GridState.NORMAL
        self.fault_remaining = 0
        if seed is not None:
            random.seed(seed)

    def next_reading(self) -> GridReading:
        self.tick += 1
        if self.fault_remaining <= 0:
            if random.random() < self.fault_probability:
                self._start_fault()
            else:
                self.current_state = GridState.NORMAL
        else:
            self.fault_remaining -= 1
        return self._generate()

    def force_fault(self, fault_type: GridState, duration: int = 10):
        self.current_state = fault_type
        self.fault_remaining = duration

    def _start_fault(self):
        faults = [s for s in GridState if s != GridState.NORMAL]
        self.current_state = random.choice(faults)
        self.fault_remaining = random.randint(5, 20)

    def _generate(self) -> GridReading:
        s = self.current_state
        if s == GridState.NORMAL:
            v, i, f, t, pf = 415+random.gauss(0,3), 82+random.gauss(0,4), 50+random.gauss(0,0.03), 42+random.gauss(0,2), 0.95+random.gauss(0,0.01)
        elif s == GridState.VOLTAGE_SAG:
            v, i, f, t, pf = random.uniform(340,385), 82+random.gauss(0,4), 50+random.gauss(0,0.03), 42+random.gauss(0,2), 0.95+random.gauss(0,0.01)
        elif s == GridState.VOLTAGE_SWELL:
            v, i, f, t, pf = random.uniform(440,470), 82+random.gauss(0,4), 50+random.gauss(0,0.03), 42+random.gauss(0,2), 0.95+random.gauss(0,0.01)
        elif s == GridState.OVERCURRENT:
            v, i, f, t, pf = 415+random.gauss(0,3), random.uniform(130,180), 50+random.gauss(0,0.03), 42+random.gauss(0,2), 0.95+random.gauss(0,0.01)
        elif s == GridState.FREQUENCY_DROP:
            v, i, f, t, pf = 415+random.gauss(0,3), 82+random.gauss(0,4), random.uniform(48.0,49.2), 42+random.gauss(0,2), 0.95+random.gauss(0,0.01)
        elif s == GridState.OVERTEMPERATURE:
            v, i, f, t, pf = 415+random.gauss(0,3), 82+random.gauss(0,4), 50+random.gauss(0,0.03), random.uniform(70,95), 0.95+random.gauss(0,0.01)
        elif s == GridState.LOW_POWER_FACTOR:
            v, i, f, t, pf = 415+random.gauss(0,3), 82+random.gauss(0,4), 50+random.gauss(0,0.03), 42+random.gauss(0,2), random.uniform(0.55,0.78)
        elif s == GridState.MULTI_FAULT:
            v, i, f, t, pf = random.uniform(340,385), random.uniform(130,180), random.uniform(48.0,49.5), random.uniform(65,95), random.uniform(0.55,0.78)

        return GridReading(
            device_id=self.device_id,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            voltage_v=v, current_a=i, frequency_hz=f,
            temperature_c=t, power_factor=max(0.0, min(1.0, pf)),
            grid_state=s.value,
        )


if __name__ == "__main__":
    gen = GridDataGenerator(fault_probability=0.15, seed=42)
    print(f"{'#':>3}  {'State':<20} {'V':>8} {'I':>7} {'Hz':>7} {'T':>6} {'PF':>6} {'Lbl':>4}")
    print("-" * 72)
    for n in range(1, 21):
        r = gen.next_reading()
        p = r.to_labeled_record()
        flag = "⚠" if p["label"] == 0 else " "
        print(f"{flag}{n:2d}  {r.grid_state:<20} {p['voltage_v']:8.2f} {p['current_a']:7.2f} {p['frequency_hz']:7.3f} {p['temperature_c']:6.1f} {p['power_factor']:6.3f} {p['label']:4d}")
