"""
network_impairments.py
Simulates network conditions: latency, packet loss, duplicates,
out-of-order delivery, disconnect/reconnect.
"""

import time
import random
import logging
from collections import deque
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class NetworkConfig:
    # latency
    latency_enabled:    bool  = False
    latency_mean_sec:   float = 0.5
    latency_sigma_sec:  float = 0.3
    latency_mode:       str   = "gaussian"  # gaussian | burst
    latency_burst_pool: list  = field(default_factory=lambda: [0,0,0,0,2,5,10,15])
    # packet loss
    loss_enabled:       bool  = False
    loss_probability:   float = 0.10
    # duplicates
    duplicate_enabled:  bool  = False
    duplicate_prob:     float = 0.05
    # out of order
    reorder_enabled:    bool  = False
    reorder_buffer_size: int  = 5
    # disconnect
    disconnect_enabled: bool  = False
    disconnect_prob:    float = 0.02
    disconnect_duration: float = 20.0
    max_buffer_size:    int   = 500


class NetworkImpairments:

    def __init__(self, config: NetworkConfig = None):
        self.cfg     = config or NetworkConfig()
        self._reorder_buffer = deque()
        self._offline        = False
        self._online_at      = None
        self._offline_buffer = deque()
        self._stats = {"sent":0,"dropped":0,"delayed":0,"duplicated":0,"reordered":0,"buffered":0}

    def pre_publish(self, payload: dict) -> list:
        """Returns list of payloads to send. Empty = dropped."""
        if self._handle_disconnect(payload):
            return []

        self._inject_latency()

        if self._inject_loss():
            return []

        packets = [payload]

        if self.cfg.duplicate_enabled and random.random() < self.cfg.duplicate_prob:
            packets.append(payload)
            self._stats["duplicated"] += 1

        if self.cfg.reorder_enabled:
            packets = self._inject_reorder(packets)

        self._stats["sent"] += len(packets)
        return packets

    def get_stats(self) -> dict:
        return self._stats.copy()

    def is_offline(self) -> bool:
        return self._offline

    def flush_offline_buffer(self, publish_fn):
        if self._offline_buffer:
            log.info(f"Flushing {len(self._offline_buffer)} buffered messages after reconnect")
            while self._offline_buffer:
                publish_fn(self._offline_buffer.popleft())
                time.sleep(0.05)

    def _inject_latency(self):
        if not self.cfg.latency_enabled:
            return
        if self.cfg.latency_mode == "burst":
            delay = random.choice(self.cfg.latency_burst_pool)
        else:
            delay = max(0, random.gauss(self.cfg.latency_mean_sec, self.cfg.latency_sigma_sec))
        if delay > 0:
            self._stats["delayed"] += 1
            time.sleep(delay)

    def _inject_loss(self) -> bool:
        if self.cfg.loss_enabled and random.random() < self.cfg.loss_probability:
            self._stats["dropped"] += 1
            return True
        return False

    def _inject_reorder(self, packets: list) -> list:
        for p in packets:
            self._reorder_buffer.append(p)
        if len(self._reorder_buffer) >= self.cfg.reorder_buffer_size:
            batch = list(self._reorder_buffer)
            random.shuffle(batch)
            self._reorder_buffer.clear()
            self._stats["reordered"] += 1
            return batch
        return []

    def _handle_disconnect(self, payload: dict) -> bool:
        if self._offline:
            if time.time() >= self._online_at:
                self._offline = False
                log.info("Network reconnected")
            else:
                if len(self._offline_buffer) < self.cfg.max_buffer_size:
                    self._offline_buffer.append(payload)
                    self._stats["buffered"] += 1
                return True

        if self.cfg.disconnect_enabled and random.random() < self.cfg.disconnect_prob:
            self._offline   = True
            self._online_at = time.time() + self.cfg.disconnect_duration
            log.warning(f"Network disconnected — offline for {self.cfg.disconnect_duration}s")
            self._offline_buffer.append(payload)
            return True

        return False
