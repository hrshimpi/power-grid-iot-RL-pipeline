"""
MQTT publisher — Power Grid Voltage Control.

Works in two modes:
  STANDALONE (no modules folder needed):
    python publish_to_iot.py --count 5
    python publish_to_iot.py --count 5 --inject voltage_sag

  SECURITY/NETWORK TEST MODE (requires modules/ folder):
    python publish_to_iot.py --count 20 --mode noisy
    python publish_to_iot.py --count 20 --mode network --loss 0.10
    python publish_to_iot.py --count 20 --mode attack
    python publish_to_iot.py --count 20 --mode chaos

Modes:
  happy    clean data, ideal network (default)
  noisy    Gaussian noise + sensor drift
  network  latency + packet loss + duplicates + disconnect
  attack   false data + replay + tampering + DoS
  chaos    everything simultaneously
"""

import json
import time
import argparse
import logging
import signal
import sys
import os
import copy
import hmac
import hashlib
import uuid

from awscrt import io, mqtt
from awsiot import mqtt_connection_builder
from data_generator import GridDataGenerator, GridState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── try loading security/network modules (optional) ───────────
MODULES_AVAILABLE = False
try:
    from modules.noise_injector      import NoiseInjector, NoiseConfig
    from modules.network_impairments import NetworkImpairments, NetworkConfig
    from modules.attack_simulator    import AttackSimulator, AttackConfig
    MODULES_AVAILABLE = True
    log.info("Security/network modules loaded")
except ImportError:
    log.info("Modules folder not found — running in standalone mode")

# ── defaults ──────────────────────────────────────────────────
# IOT_CERT / IOT_KEY use a {device_id} placeholder resolved after argparse,
# same as IOT_TOPIC — matches the certs/<device_id>/... layout that Terraform
# generates and that gets copied into edge_simulator/colab/certs/.
DEFAULT_ENDPOINT  = os.environ.get("IOT_ENDPOINT",  "YOUR-ENDPOINT-ats.iot.us-east-1.amazonaws.com")
DEFAULT_CERT      = os.environ.get("IOT_CERT",      "./certs/{device_id}/cert.pem")
DEFAULT_KEY       = os.environ.get("IOT_KEY",       "./certs/{device_id}/private.key")
DEFAULT_ROOT_CA   = os.environ.get("IOT_ROOT_CA",   "./certs/root-CA.crt")
DEFAULT_CLIENT_ID = os.environ.get("IOT_CLIENT_ID", "edge-device-001")
DEFAULT_TOPIC     = os.environ.get("IOT_TOPIC",     "grid/{device_id}/telemetry")
HMAC_SECRET       = os.environ.get("HMAC_SECRET",   "").encode()

running = True
# seeded from epoch ms, not 0 — Lambda's replay check remembers the last seq it
# saw per device for as long as its container stays warm, so restarting this
# script and counting from 1 again would get rejected as a replay. Real time
# only moves forward, so this is always higher than whatever a previous run
# (or a still-warm container) last saw.
sequence_number = int(time.time() * 1000)


# ── security: HMAC signing (only if HMAC_SECRET is set) ───────
def sign_payload(payload: dict) -> dict:
    if not HMAC_SECRET:
        return payload
    out = copy.deepcopy(payload)
    out["seq"]   = sequence_number
    out["nonce"] = str(uuid.uuid4())
    canonical    = json.dumps({k: v for k, v in out.items()}, sort_keys=True, separators=(",", ":"))
    out["signature"] = hmac.new(HMAC_SECRET, canonical.encode(), hashlib.sha256).hexdigest()
    return out


# ── callbacks ─────────────────────────────────────────────────
def on_connection_interrupted(connection, error, **kwargs):
    log.warning(f"Connection interrupted: {error}")

def on_connection_resumed(connection, return_code, session_present, **kwargs):
    log.info(f"Connection resumed: {return_code}")

def on_shadow_delta(topic, payload, dup, qos, retain, **kwargs):
    try:
        message  = json.loads(payload.decode("utf-8"))
        state    = message.get("state", {})
        reported = state.get("reported", state)
        action   = reported.get("action", "-")
        name     = reported.get("action_name", "-")
        conf     = reported.get("confidence", "-")
        urgency  = "🔴 CRITICAL" if action in [1,2,3] else "🟡 MONITOR" if action == 4 else "🟢 NORMAL"

        print("\n" + "=" * 60)
        print(f"RECEIVED FROM CLOUD — RL DECISION  {urgency}")
        print(f"  action        : {action} — {name}")
        print(f"  confidence    : {conf}")
        print(f"  voltage_echo  : {reported.get('voltage_v', '-')} V")
        print(f"  current_echo  : {reported.get('current_a', '-')} A")
        print(f"  frequency     : {reported.get('frequency_hz', '-')} Hz")
        print(f"  temperature   : {reported.get('temperature_c', '-')} C")
        print(f"  power_factor  : {reported.get('power_factor', '-')}")
        print(f"  processed_at  : {reported.get('processed_at', '-')}")
        print("=" * 60 + "\n")

        with open("latest_prediction.json", "w") as f:
            json.dump(reported, f, indent=2)
    except Exception as e:
        log.error(f"Shadow parse error: {e}")


# ── build mode configs ─────────────────────────────────────────
def build_configs(args):
    if not MODULES_AVAILABLE:
        return None, None, None

    nc = NoiseConfig()
    nw = NetworkConfig()
    ac = AttackConfig()
    mode = getattr(args, "mode", "happy")

    if mode == "noisy":
        nc.enabled = True
        nc.drift_enabled = True

    elif mode == "network":
        nw.latency_enabled   = True
        nw.latency_mean_sec  = float(getattr(args, "latency", None) or 0.5)
        nw.loss_enabled      = True
        nw.loss_probability  = float(getattr(args, "loss", None) or 0.10)
        nw.duplicate_enabled = True
        nw.reorder_enabled   = True
        nw.disconnect_enabled = True

    elif mode == "attack":
        ac.false_data_enabled = True
        ac.replay_enabled     = True
        ac.tamper_enabled     = True
        ac.ts_enabled         = True
        ac.dos_enabled        = bool(getattr(args, "dos", False))

    elif mode == "chaos":
        nc.enabled = True; nc.drift_enabled = True; nc.corruption_prob = 0.02
        nw.latency_enabled = True; nw.latency_mode = "burst"
        nw.loss_enabled = True; nw.duplicate_enabled = True
        nw.reorder_enabled = True; nw.disconnect_enabled = True
        ac.false_data_enabled = True; ac.replay_enabled = True
        ac.tamper_enabled = True; ac.poison_enabled = True
        ac.ts_enabled = True

    # individual overrides
    if getattr(args, "loss", None):
        nw.loss_enabled = True; nw.loss_probability = float(args.loss)
    if getattr(args, "latency", None):
        nw.latency_enabled = True; nw.latency_mean_sec = float(args.latency)
    if getattr(args, "dos", False):
        ac.dos_enabled = True
    if getattr(args, "replay", False):
        ac.replay_enabled = True
    if getattr(args, "false_data", False):
        ac.false_data_enabled = True

    return NoiseInjector(nc), NetworkImpairments(nw), AttackSimulator(ac)


# ── publish loop ──────────────────────────────────────────────
def publish_loop(args, connection, topic, generator, noise, network, attacks):
    global running, sequence_number

    def _publish_raw(payload: dict):
        future, _ = connection.publish(
            topic=topic,
            payload=json.dumps(payload),
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )
        future.result()

    published = 0
    saved     = []
    count     = args.count

    log.info(f"Mode:       {getattr(args, 'mode', 'standalone')}")
    log.info(f"Count:      {count} | Fault rate: {args.fault_rate*100:.0f}%")
    log.info(f"Modules:    {'active' if MODULES_AVAILABLE else 'not loaded — standalone mode'}")
    log.info("─" * 60)

    while running:
        if count > 0 and published >= count:
            break

        published      += 1
        sequence_number += 1

        # 1. generate reading
        reading = generator.next_reading()
        payload = reading.to_mqtt_payload()

        # 2. apply noise (if modules active)
        if noise:
            payload = noise.inject(payload)

        # 3. sign with HMAC + seq (if HMAC_SECRET set)
        payload = sign_payload(payload)

        # 4. apply attacks AFTER signing
        payloads_to_send = attacks.process(payload, _publish_raw) if attacks else [payload]

        # 5. apply network impairments
        final = []
        for p in payloads_to_send:
            result = network.pre_publish(p) if network else [p]
            final.extend(result)

        # 6. flush offline buffer if reconnected
        if network and not network.is_offline():
            network.flush_offline_buffer(_publish_raw)

        # 7. publish surviving packets
        for p in final:
            try:
                _publish_raw(p)
            except Exception as e:
                log.error(f"Publish error: {e}")

        # 8. log
        state = reading.grid_state
        flag  = "⚠ FAULT" if state != "normal" else "  OK   "
        log.info(
            f"[{published:5d}] {flag} | "
            f"V={payload.get('voltage_v',0):7.2f} "
            f"I={payload.get('current_a',0):6.2f} "
            f"f={payload.get('frequency_hz',0):6.3f} "
            f"T={payload.get('temperature_c',0):5.1f} "
            f"PF={payload.get('power_factor',0):.3f} "
            f"sent={len(final)} | {state}"
        )

        if args.save:
            saved.append(reading.to_labeled_record())

        time.sleep(args.interval)

    # stats
    if network:
        log.info(f"Network stats: {network.get_stats()}")
    if attacks:
        log.info(f"Attack stats:  {attacks.get_stats()}")

    if args.save and saved:
        with open(args.save, "w") as f:
            json.dump(saved, f, indent=2)
        log.info(f"Saved {len(saved)} records to {args.save}")


# ── main ──────────────────────────────────────────────────────
def main():
    global running

    parser = argparse.ArgumentParser(description="Grid voltage MQTT publisher")

    # connection
    parser.add_argument("--endpoint",        default=DEFAULT_ENDPOINT)
    parser.add_argument("--cert",            default=DEFAULT_CERT)
    parser.add_argument("--key",             default=DEFAULT_KEY)
    parser.add_argument("--root-ca",         default=DEFAULT_ROOT_CA)
    parser.add_argument("--client-id",       default=DEFAULT_CLIENT_ID)
    parser.add_argument("--topic",           default=DEFAULT_TOPIC)

    # data
    parser.add_argument("--count",           type=int,   default=5)
    parser.add_argument("--interval",        type=float, default=1.0)
    parser.add_argument("--fault-rate",      type=float, default=0.0)
    parser.add_argument("--seed",            type=int,   default=None)
    parser.add_argument("--inject",          type=str,   default=None,
                        choices=[s.value for s in GridState if s != GridState.NORMAL])
    parser.add_argument("--inject-duration", type=int,   default=10)
    parser.add_argument("--save",            type=str,   default=None)

    # mode (only used if modules/ folder present)
    parser.add_argument("--mode", default="happy",
                        choices=["happy","noisy","network","attack","chaos"])
    parser.add_argument("--loss",        type=float, default=None)
    parser.add_argument("--latency",     type=float, default=None)
    parser.add_argument("--dos",         action="store_true")
    parser.add_argument("--replay",      action="store_true")
    parser.add_argument("--false-data",  action="store_true")

    args = parser.parse_args()

    topic     = args.topic.replace("{device_id}", args.client_id)
    args.cert = args.cert.replace("{device_id}", args.client_id)
    args.key  = args.key.replace("{device_id}", args.client_id)

    for path, name in [(args.cert,"cert"),(args.key,"key"),(args.root_ca,"root CA")]:
        if not os.path.isfile(path):
            print(f"ERROR: {name} not found: {path}")
            sys.exit(1)

    if not HMAC_SECRET:
        log.warning("HMAC_SECRET not set — payloads will be sent unsigned")

    def sig_handler(sig, frame):
        global running
        log.info("Stopping...")
        running = False
    signal.signal(signal.SIGINT,  sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    generator = GridDataGenerator(
        device_id=args.client_id,
        fault_probability=args.fault_rate,
        seed=args.seed,
    )
    if args.inject:
        generator.force_fault(GridState(args.inject), args.inject_duration)
        log.info(f"Fault injected: {args.inject}")

    noise, network, attacks = build_configs(args)

    # connect
    log.info(f"Connecting to {args.endpoint}...")
    elg = io.EventLoopGroup(1)
    hr  = io.DefaultHostResolver(elg)
    cb  = io.ClientBootstrap(elg, hr)

    connection = mqtt_connection_builder.mtls_from_path(
        endpoint=args.endpoint, cert_filepath=args.cert,
        pri_key_filepath=args.key, ca_filepath=args.root_ca,
        client_bootstrap=cb, client_id=args.client_id,
        clean_session=False, keep_alive_secs=60,
        on_connection_interrupted=on_connection_interrupted,
        on_connection_resumed=on_connection_resumed,
    )
    connection.connect().result()
    log.info("Connected to IoT Core")

    shadow_topic = f"$aws/things/{args.client_id}/shadow/update/accepted"
    connection.subscribe(shadow_topic, mqtt.QoS.AT_LEAST_ONCE, on_shadow_delta)[0].result()
    log.info(f"Subscribed to shadow: {shadow_topic}")

    publish_loop(args, connection, topic, generator, noise, network, attacks)

    log.info("Waiting 5s for shadow responses...")
    time.sleep(5)
    connection.disconnect().result()
    log.info("Done")


if __name__ == "__main__":
    main()
