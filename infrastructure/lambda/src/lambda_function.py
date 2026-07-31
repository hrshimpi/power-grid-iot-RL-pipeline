"""
Grid IoT RL — Lambda inference function.
Deployed automatically by Terraform from infrastructure/lambda/src/.

Security controls:
  1. HMAC-SHA256 signature verification (fetched from Secrets Manager)
  2. Replay attack protection (timestamp + sequence number)
  3. Input validation + anomaly detection
  4. Runs inside VPC private subnet

Flow: IoT Core → Lambda → SageMaker → Device Shadow + S3 + SNS
"""

import boto3
import json
import time
import hmac
import hashlib
import logging
import os
from datetime import datetime, timezone, timedelta

log = logging.getLogger()
log.setLevel(logging.INFO)

# ── AWS clients ───────────────────────────────────────────────
region   = os.environ.get("AWS_REGION", "us-east-1")
s3       = boto3.client("s3")
sns      = boto3.client("sns",      region_name=region)
iot_data = boto3.client("iot-data", region_name=region)

# ── env vars ──────────────────────────────────────────────────
S3_BUCKET          = os.environ.get("S3_BUCKET",          "")
SNS_TOPIC_ARN      = os.environ.get("SNS_TOPIC_ARN",      "")
HMAC_SECRET_ARN    = os.environ.get("HMAC_SECRET_ARN",    "")
SAGEMAKER_ENDPOINT = os.environ.get("SAGEMAKER_ENDPOINT", "")

# ── load HMAC secret from Secrets Manager (cached) ───────────
_HMAC_SECRET = None

def get_hmac_secret() -> bytes:
    global _HMAC_SECRET
    if _HMAC_SECRET:
        return _HMAC_SECRET
    if not HMAC_SECRET_ARN:
        return b""
    sm   = boto3.client("secretsmanager", region_name=region)
    resp = sm.get_secret_value(SecretId=HMAC_SECRET_ARN)
    data = json.loads(resp["SecretString"])
    _HMAC_SECRET = data["hmac_secret"].encode()
    return _HMAC_SECRET

# ── in-memory replay protection (production: replace with DynamoDB) ──
_seen_sequences: dict = {}
_msg_counts:     dict = {}
_last_readings:  dict = {}

# ── validation bounds ─────────────────────────────────────────
BOUNDS = {
    "voltage_v":     (200,  600),
    "current_a":     (0,    300),
    "frequency_hz":  (45,   55),
    "temperature_c": (-20,  150),
    "power_factor":  (0,    1),
}

MAX_VOLTAGE_JUMP  = 50.0   # V  per reading
MAX_TIMESTAMP_AGE = 60     # seconds
MAX_MSG_PER_MIN   = 120    # per device

ACTION_NAMES = {
    0: "no_action",
    1: "shed_load",
    2: "switch_backup",
    3: "reduce_generation",
    4: "alert_operator",
}


# ══ SECURITY GATE 1 — HMAC ════════════════════════════════════
def verify_signature(event: dict) -> tuple:
    secret = get_hmac_secret()
    if not secret:
        log.info("HMAC_SECRET_ARN not set — skipping signature check")
        return True, "hmac_disabled"

    signature = event.get("signature")
    if not signature:
        return False, "missing_signature"

    base      = {k: v for k, v in event.items() if k != "signature"}
    canonical = json.dumps(base, sort_keys=True, separators=(",", ":"))
    expected  = hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, signature):
        return False, "invalid_signature"
    return True, "ok"


# ══ SECURITY GATE 2 — Replay ══════════════════════════════════
def check_replay(event: dict, device_id: str) -> tuple:
    ts_str = event.get("timestamp", "")
    seq    = event.get("seq")

    try:
        ts  = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > MAX_TIMESTAMP_AGE:
            return False, f"stale_timestamp_age={age:.0f}s"
        if age < -10:
            return False, "future_timestamp"
    except Exception:
        if ts_str:
            return False, "invalid_timestamp"

    if seq is not None:
        last = _seen_sequences.get(device_id, -1)
        if int(seq) <= last:
            return False, f"replay_seq={seq}_last={last}"
        _seen_sequences[device_id] = int(seq)

    return True, "ok"


# ══ SECURITY GATE 3 — Input validation ════════════════════════
def validate_inputs(event: dict, device_id: str) -> tuple:
    for field, (lo, hi) in BOUNDS.items():
        val = event.get(field)
        if val is None:
            return False, f"missing_field:{field}"
        try:
            val = float(val)
        except (TypeError, ValueError):
            return False, f"non_numeric:{field}"
        if not (lo <= val <= hi):
            return False, f"out_of_range:{field}={val}"

    # anomaly — sudden voltage jump
    last = _last_readings.get(device_id)
    if last:
        v_jump = abs(float(event.get("voltage_v", 415)) - float(last.get("voltage_v", 415)))
        if v_jump > MAX_VOLTAGE_JUMP:
            log.warning(f"ANOMALY: voltage jump {v_jump:.1f}V on {device_id}")

    # rate limiting
    now_ts = time.time()
    count, win = _msg_counts.get(device_id, (0, now_ts))
    if now_ts - win < 60:
        count += 1
        if count > MAX_MSG_PER_MIN:
            return False, f"rate_limit:{count}/min"
    else:
        count, win = 1, now_ts
    _msg_counts[device_id]  = (count, win)
    _last_readings[device_id] = event

    return True, "ok"


# ══ SageMaker inference ═══════════════════════════════════════
def invoke_sagemaker(event: dict) -> tuple:
    action, name, confidence, q_values = 0, "no_action", 0.0, []
    if not SAGEMAKER_ENDPOINT:
        log.info("SAGEMAKER_ENDPOINT not set — skipping inference")
        return action, name, confidence, q_values
    try:
        sm   = boto3.client("sagemaker-runtime", region_name=region)
        resp = sm.invoke_endpoint(
            EndpointName=SAGEMAKER_ENDPOINT,
            ContentType="application/json",
            Body=json.dumps({
                "voltage_v":     float(event["voltage_v"]),
                "current_a":     float(event["current_a"]),
                "frequency_hz":  float(event["frequency_hz"]),
                "temperature_c": float(event["temperature_c"]),
                "power_factor":  float(event["power_factor"]),
            }),
        )
        result     = json.loads(resp["Body"].read())
        action     = int(result["action"])
        name       = result.get("action_name", ACTION_NAMES.get(action, "unknown"))
        confidence = float(result["confidence"])
        q_values   = result.get("q_values", [])
        log.info(f"RL Agent: action={action} ({name}) conf={confidence:.3f}")
    except Exception as e:
        log.warning(f"SageMaker error: {e}")
    return action, name, confidence, q_values


# ══ Helpers ═══════════════════════════════════════════════════
def detect_fault(event: dict) -> str:
    v, i, f, t, pf = (
        float(event.get("voltage_v",    415)),
        float(event.get("current_a",     82)),
        float(event.get("frequency_hz",  50)),
        float(event.get("temperature_c", 42)),
        float(event.get("power_factor", 0.95)),
    )
    faults = []
    if v < 395:   faults.append("voltage_sag")
    if v > 435:   faults.append("voltage_swell")
    if i > 115:   faults.append("overcurrent")
    if f < 49.5:  faults.append("frequency_drop")
    if t > 65:    faults.append("overtemperature")
    if pf < 0.85: faults.append("low_power_factor")
    if len(faults) > 1: return "multi_fault"
    return faults[0] if faults else "normal"


def security_alert(device_id: str, alert_type: str, reason: str, now: datetime):
    if not SNS_TOPIC_ARN:
        return
    try:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"[SECURITY] {alert_type} from {device_id}",
            Message=f"Device: {device_id}\nAlert: {alert_type}\nReason: {reason}\nTime: {now.isoformat()}\n",
        )
    except Exception as e:
        log.warning(f"Security SNS failed: {e}")


# ══ MAIN HANDLER ══════════════════════════════════════════════
def lambda_handler(event, context):
    now       = datetime.now(timezone.utc)
    device_id = event.get("device_id", "unknown")

    log.info("=" * 60)
    log.info(f"RECEIVED from {device_id}")
    log.info(f"  V={event.get('voltage_v')} I={event.get('current_a')} "
             f"Hz={event.get('frequency_hz')} T={event.get('temperature_c')} "
             f"PF={event.get('power_factor')} seq={event.get('seq','N/A')}")
    log.info("=" * 60)

    # gate 1: HMAC
    sig_ok, sig_reason = verify_signature(event)
    if not sig_ok:
        log.error(f"SECURITY REJECT — signature: {sig_reason} device={device_id}")
        security_alert(device_id, "signature_failure", sig_reason, now)
        return {"statusCode": 403, "reason": sig_reason}
    log.info(f"✓ Signature: {sig_reason}")

    # gate 2: replay
    replay_ok, replay_reason = check_replay(event, device_id)
    if not replay_ok:
        log.error(f"SECURITY REJECT — replay: {replay_reason} device={device_id}")
        security_alert(device_id, "replay_attack", replay_reason, now)
        return {"statusCode": 403, "reason": replay_reason}
    log.info(f"✓ Replay check: {replay_reason}")

    # gate 3: input validation
    valid, val_reason = validate_inputs(event, device_id)
    if not valid:
        log.error(f"SECURITY REJECT — validation: {val_reason} device={device_id}")
        security_alert(device_id, "input_validation_failure", val_reason, now)
        return {"statusCode": 422, "reason": val_reason}
    log.info(f"✓ Input valid")

    fault_type = detect_fault(event)
    log.info(f"  Grid state: {fault_type}")

    # inference
    action, action_name, confidence, q_values = invoke_sagemaker(event)

    # enrich record
    record = {
        **event,
        "fault_type":     fault_type,
        "action":         action,
        "action_name":    action_name,
        "confidence":     confidence,
        "q_values":       json.dumps(q_values),
        "signature_valid": sig_ok,
        "replay_check":   "passed",
        "processed_at":   now.isoformat(),
    }

    # save to S3
    s3_key = ""
    if S3_BUCKET:
        try:
            s3_key = (
                f"readings/year={now.year}/month={now.month:02d}/"
                f"day={now.day:02d}/{device_id}_{int(time.time()*1000)}.json"
            )
            s3.put_object(
                Bucket=S3_BUCKET, Key=s3_key,
                Body=json.dumps(record),
                ContentType="application/json",
            )
            log.info(f"Saved to S3: {s3_key}")
        except Exception as e:
            log.warning(f"S3 failed: {e}")

    # update Device Shadow
    try:
        iot_data.update_thing_shadow(
            thingName=device_id,
            payload=json.dumps({"state": {"reported": {
                "voltage_v":      event.get("voltage_v"),
                "current_a":      event.get("current_a"),
                "frequency_hz":   event.get("frequency_hz"),
                "temperature_c":  event.get("temperature_c"),
                "power_factor":   event.get("power_factor"),
                "fault_type":     fault_type,
                "action":         action,
                "action_name":    action_name,
                "confidence":     confidence,
                "signature_valid": sig_ok,
                "replay_check":   "passed",
                "status":         "processed",
                "processed_at":   now.isoformat(),
                "last_s3_object": s3_key,
            }}}).encode("utf-8"),
        )
        log.info(f"Shadow updated for {device_id}")
    except Exception as e:
        log.warning(f"Shadow failed: {e}")

    # SNS alert
    if action != 0 and SNS_TOPIC_ARN:
        try:
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=f"[GRID ALERT] {action_name.upper()} on {device_id}",
                Message=(
                    f"Device:    {device_id}\n"
                    f"Fault:     {fault_type}\n"
                    f"Action:    {action_name} (id={action})\n"
                    f"Confidence:{confidence:.3f}\n"
                    f"V={event.get('voltage_v')} I={event.get('current_a')} "
                    f"Hz={event.get('frequency_hz')} T={event.get('temperature_c')} "
                    f"PF={event.get('power_factor')}\n"
                    f"Time:      {now.isoformat()}\n"
                ),
            )
        except Exception as e:
            log.warning(f"SNS alert failed: {e}")

    return {"statusCode": 200, "device_id": device_id, "action": action}
