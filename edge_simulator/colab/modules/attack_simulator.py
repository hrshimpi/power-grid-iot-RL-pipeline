"""
attack_simulator.py — Grid voltage schema
Simulates cyberattacks: false data, replay, tampering,
data poisoning, timestamp manipulation, DoS flood.
"""

import copy
import random
import time
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)


@dataclass
class AttackConfig:
    # false data — sends plausible but fake grid values
    false_data_enabled:  bool  = False
    false_data_prob:     float = 0.05
    false_v_range:       tuple = (380, 400)  # looks almost normal
    false_i_range:       tuple = (50, 60)

    # replay
    replay_enabled:      bool  = False
    replay_prob:         float = 0.05
    replay_buffer_size:  int   = 20

    # tamper — change field WITHOUT re-signing HMAC
    tamper_enabled:      bool  = False
    tamper_prob:         float = 0.03

    # data poisoning — impossible values
    poison_enabled:      bool  = False
    poison_prob:         float = 0.02
    poison_v:            float = -999.0
    poison_i:            float = -999.0

    # timestamp manipulation
    ts_enabled:          bool  = False
    ts_prob:             float = 0.03
    ts_offset_sec:       float = -3600.0  # 1 hour old

    # DoS flood
    dos_enabled:         bool  = False
    dos_burst_size:      int   = 200
    dos_interval_sec:    float = 0.001
    dos_trigger_prob:    float = 0.005


class AttackSimulator:

    def __init__(self, config: AttackConfig = None):
        self.cfg = config or AttackConfig()
        self._replay_buffer = deque(maxlen=self.cfg.replay_buffer_size)
        self._stats = {"false_data":0,"replays":0,"dos_packets":0,"tampered":0,"poisoned":0,"ts_manip":0}

    def process(self, payload: dict, publish_fn) -> list:
        results = [copy.deepcopy(payload)]
        self._replay_buffer.append(copy.deepcopy(payload))

        # false data
        if self.cfg.false_data_enabled and random.random() < self.cfg.false_data_prob:
            results = [self._false_data(results[0])]
            self._stats["false_data"] += 1
            log.warning("[ATTACK] False data injected")

        # tamper — no re-sign, HMAC will catch
        elif self.cfg.tamper_enabled and random.random() < self.cfg.tamper_prob:
            results = [self._tamper(results[0])]
            self._stats["tampered"] += 1
            log.warning("[ATTACK] Packet tampered")

        # poisoning
        elif self.cfg.poison_enabled and random.random() < self.cfg.poison_prob:
            results = [self._poison(results[0])]
            self._stats["poisoned"] += 1
            log.warning("[ATTACK] Data poisoned")

        # timestamp manipulation
        if self.cfg.ts_enabled and random.random() < self.cfg.ts_prob:
            results = [self._ts_manipulate(results[0])]
            self._stats["ts_manip"] += 1
            log.warning("[ATTACK] Timestamp manipulated")

        # replay — add old packet alongside current
        if self.cfg.replay_enabled and random.random() < self.cfg.replay_prob:
            old = self._get_replay()
            if old:
                results.append(old)
                self._stats["replays"] += 1
                log.warning("[ATTACK] Replay packet injected")

        # DoS
        if self.cfg.dos_enabled and random.random() < self.cfg.dos_trigger_prob:
            self._flood(publish_fn, payload)

        return results

    def get_stats(self) -> dict:
        return self._stats.copy()

    def _false_data(self, p: dict) -> dict:
        out = copy.deepcopy(p)
        out["voltage_v"] = round(random.uniform(*self.cfg.false_v_range), 2)
        out["current_a"] = round(random.uniform(*self.cfg.false_i_range), 2)
        return out

    def _tamper(self, p: dict) -> dict:
        out = copy.deepcopy(p)
        out["voltage_v"] = round(out.get("voltage_v", 415) + 50, 2)  # spike voltage
        # signature NOT updated — Lambda HMAC catches this
        return out

    def _poison(self, p: dict) -> dict:
        out = copy.deepcopy(p)
        out["voltage_v"] = self.cfg.poison_v
        out["current_a"] = self.cfg.poison_i
        return out

    def _ts_manipulate(self, p: dict) -> dict:
        out = copy.deepcopy(p)
        old = datetime.now(timezone.utc) + timedelta(seconds=self.cfg.ts_offset_sec)
        out["timestamp"] = old.strftime("%Y-%m-%dT%H:%M:%SZ")
        return out

    def _get_replay(self):
        if len(self._replay_buffer) < 2:
            return None
        return copy.deepcopy(random.choice(list(self._replay_buffer)[:-1]))

    def _flood(self, publish_fn, base: dict):
        log.warning(f"[ATTACK] DoS flood: {self.cfg.dos_burst_size} packets")
        for i in range(self.cfg.dos_burst_size):
            msg = copy.deepcopy(base)
            msg["_dos_seq"] = i
            try:
                publish_fn(msg)
            except Exception:
                pass
            time.sleep(self.cfg.dos_interval_sec)
            self._stats["dos_packets"] += 1
