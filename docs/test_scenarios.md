# Test Scenarios

| # | Scenario | Command | Outcome |
|---|---|---|---|
| 1 | Normal condition | `python publish_to_iot.py --count 5 --mode happy` | ✅ Accepted — saved to S3 |
| 2 | Noisy sensors | `python publish_to_iot.py --count 5 --mode noisy` | ✅ Accepted — noisy but valid |
| 3 | Packet loss 10% | `python publish_to_iot.py --count 10 --loss 0.10` | ⚠️ ~1 lost |
| 4 | Packet loss 30% | `python publish_to_iot.py --count 10 --loss 0.30` | ⚠️ ~3 lost |
| 5 | High latency | `python publish_to_iot.py --count 5 --latency 3.0` | ✅ Accepted — delayed |
| 6 | Duplicate packets | `python publish_to_iot.py --count 10 --duplicate` | ⚠️ 2nd copy rejected by seq |
| 7 | Out of order | `python publish_to_iot.py --count 10 --reorder` | ⚠️ Accepted, out of order in S3 |
| 8 | Disconnect reconnect | `python publish_to_iot.py --count 20 --disconnect` | ✅ Buffered and replayed |
| 9 | Replay attack | `python publish_to_iot.py --count 10 --replay` | ❌ Rejected — stale timestamp |
| 10 | Packet tampering | `python publish_to_iot.py --count 5 --tamper` | ❌ Rejected — HMAC mismatch |
| 11 | False data injection | `python publish_to_iot.py --count 5 --false-data` | ❌ Rejected — bounds check |
| 12 | Data poisoning | `python publish_to_iot.py --count 5 --poison` | ❌ Rejected — impossible values |
| 13 | Timestamp manipulation | `python publish_to_iot.py --count 5 --ts-manipulate` | ❌ Rejected — age > 60s |
| 14 | DoS flood | `python publish_to_iot.py --count 5 --dos` | ❌ Rejected — rate limit |
| 15 | Sensor drift | `python publish_to_iot.py --count 20 --mode noisy` | ✅ Accepted — within bounds |
| 16 | Voltage sag | `python publish_to_iot.py --count 5 --inject voltage_sag` | ✅ RL → action=2 switch_backup |
| 17 | Voltage swell | `python publish_to_iot.py --count 5 --inject voltage_swell` | ✅ RL → action=3 reduce_generation |
| 18 | Overcurrent | `python publish_to_iot.py --count 5 --inject overcurrent` | ✅ RL → action=1 shed_load |
| 19 | Frequency drop | `python publish_to_iot.py --count 5 --inject frequency_drop` | ✅ RL → action=4 alert_operator |
| 20 | Multi fault | `python publish_to_iot.py --count 5 --inject multi_fault` | ✅ RL → action=4 alert_operator |
| 21 | Chaos mode | `python publish_to_iot.py --count 20 --mode chaos --fault-rate 0.2` | Mixed |
