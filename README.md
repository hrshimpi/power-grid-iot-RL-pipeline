# Grid IoT RL - Power Grid Voltage Control with Reinforcement Learning

End-to-end IoT MLOps pipeline: edge sensors → AWS cloud → DQN RL agent → real-time decisions.

## Architecture

![Architecture diagram](docs/architecture-diagram.png)

*Simplified view - the detailed flow below also includes the IoT Rule trigger,
the Device Shadow return path, and Secrets Manager/CloudWatch, omitted above for clarity.*

```
Edge Device (sensor readings every second)
       │ MQTT over TLS 1.2 (mTLS + HMAC-SHA256)
       ▼
AWS IoT Core (VPC-connected)
       │ IoT Rule → Lambda trigger
       ▼
AWS Lambda (VPC private subnet)
       ├── Security: HMAC verify + replay check + input validation
       ├── SageMaker endpoint → DQN RL action
       ├── S3 → store enriched record (JSON)
       ├── IoT Device Shadow → send action back to edge
       └── SNS → alert if fault detected
```

## Data journey — what the payload actually looks like

The diagrams above show *services*. This section traces one actual reading through every
transformation it goes through, byte by byte — pulled directly from the code
(`data_generator.py`, `publish_to_iot.py`, `lambda_function.py`, the notebook's
`inference.py`), not a simplified summary. One scenario runs through the whole thing so the
before/after at each stage is directly comparable: device `edge-device-002` reports a
**voltage sag**, and the agent responds with `switch_backup`.

```
 1  EDGE DEVICE — raw reading                                     7 fields
    generator produces one simulated sensor sample
        │
        │  sign_payload() stamps a sequence number + nonce, then HMAC-SHA256s the result
        ▼
 2  EDGE DEVICE — signed for transmission                         +3 fields → 10 total
        │
        │  published over MQTT/TLS 1.2 to grid/{device_id}/telemetry — bytes unchanged
        ▼
 3  AWS IoT CORE — MQTT ingest                                    0 change, still 10 fields
        │
        │  IoT Rule "SELECT * FROM 'grid/+/telemetry'" forwards the message verbatim
        ▼
 4  LAMBDA — event                                                0 change — this *is* stage 2's JSON
        │
        │  verify_signature() / check_replay() / validate_inputs() — read-only security
        │  gates; nothing added or removed, only pass/reject
        ▼
 5  LAMBDA → SAGEMAKER — narrowed request              10 → 5 fields, security metadata stripped
        │
        │  inference.py: input_fn() scales the 5 floats with the trained MinMaxScaler,
        │  predict_fn() runs the DQN forward pass, output_fn() returns the decision
        ▼
 6  SAGEMAKER — response                                    a new, separate object — 4 fields
        │
        │  Lambda merges: original event + this response + its own derived fields
        ▼
 7  LAMBDA — enriched record                                        10 + 8 = 18 fields
        │
        ├──▶  S3 — written verbatim as readings/year=.../month=.../day=.../{device_id}_{ts}.json
        │
        │  Lambda then picks a DIFFERENT, smaller subset for the shadow — drops
        │  seq/nonce/signature/q_values, adds status + a pointer back to the S3 object
        ▼
 8  DEVICE SHADOW — reported state                14 fields — not the same 18 that went to S3
        │
        │  AWS IoT wraps this in its own shadow envelope and publishes to
        │  $aws/things/{device_id}/shadow/update/accepted
        ▼
 9  EDGE DEVICE — shadow delta received
    on_shadow_delta() unwraps state.reported and prints/saves the decision
```

### 1 — Raw reading (`data_generator.py`)

```json
{
  "device_id": "edge-device-002",
  "timestamp": "2026-08-10T14:32:07Z",
  "voltage_v": 362.4,
  "current_a": 78.6,
  "frequency_hz": 49.98,
  "temperature_c": 41.2,
  "power_factor": 0.947
}
```
Voltage is already outside the 400–430V normal band — this is what a real sag looks like
before anything downstream has decided that yet.

### 2 — Signed for transmission (`publish_to_iot.py: sign_payload()`)

```json
{
  "device_id": "edge-device-002",
  "timestamp": "2026-08-10T14:32:07Z",
  "voltage_v": 362.4,
  "current_a": 78.6,
  "frequency_hz": 49.98,
  "temperature_c": 41.2,
  "power_factor": 0.947,
  "seq": 1755008527914,
  "nonce": "3f2a9d7c-88b1-4e2a-9c31-7a5e0b6d4f10",
  "signature": "7c2f9a1e4b6d8035f1a9c7e2b4d6f8031c9e7a5b3d1f9c7e5a3b1d9f7c5e3a1b"
}
```
**Changed:** +3 fields. `seq` and `nonce` exist purely so this exact message can never be
replayed; `signature` is the HMAC-SHA256 of every other field (sorted, so both sides compute
it identically) — this is the object that actually crosses the network.

### 3 — AWS IoT Core (MQTT ingest)

No JSON block — this stage doesn't touch the payload at all. The IoT Rule
(`SELECT * FROM 'grid/+/telemetry'`) just matches the topic and forwards the message
verbatim to Lambda. Byte-identical to stage 2.

### 4 — Lambda's `event` (`lambda_function.py: lambda_handler()`)

Also byte-identical to stage 2 — this is literally what IoT Core handed Lambda, with no
transformation yet. `verify_signature()`, `check_replay()`, and `validate_inputs()` all run
against this object next; they read it and pass/reject, but add or remove nothing.

### 5 — Narrowed for SageMaker (`lambda_function.py: invoke_sagemaker()`)

```json
{
  "voltage_v": 362.4,
  "current_a": 78.6,
  "frequency_hz": 49.98,
  "temperature_c": 41.2,
  "power_factor": 0.947
}
```
**Changed:** 10 fields → 5. `device_id`, `timestamp`, `seq`, `nonce`, `signature` are all
security/routing metadata the model has no use for — SageMaker only ever sees the 5 numbers
it was trained on.

### 6 — SageMaker's response (`inference.py: predict_fn()`)

```json
{
  "action": 2,
  "action_name": "switch_backup",
  "confidence": 0.9421,
  "q_values": [-1.82, 0.34, 3.71, 1.02, -0.55]
}
```
**Changed:** an entirely new object — nothing from the request is echoed back. Internally,
`input_fn()` first normalizes the 5 raw floats with the same `MinMaxScaler` values the
notebook fit during training, before the DQN ever sees them.

### 7 — Enriched record (`lambda_function.py`, written to S3)

```json
{
  "device_id": "edge-device-002",
  "timestamp": "2026-08-10T14:32:07Z",
  "voltage_v": 362.4,
  "current_a": 78.6,
  "frequency_hz": 49.98,
  "temperature_c": 41.2,
  "power_factor": 0.947,
  "seq": 1755008527914,
  "nonce": "3f2a9d7c-88b1-4e2a-9c31-7a5e0b6d4f10",
  "signature": "7c2f9a1e4b6d8035f1a9c7e2b4d6f8031c9e7a5b3d1f9c7e5a3b1d9f7c5e3a1b",
  "fault_type": "voltage_sag",
  "action": 2,
  "action_name": "switch_backup",
  "confidence": 0.9421,
  "q_values": "[-1.82, 0.34, 3.71, 1.02, -0.55]",
  "signature_valid": true,
  "replay_check": "passed",
  "processed_at": "2026-08-10T14:32:08.114562+00:00"
}
```
**Changed:** stage 2's 10 fields, spread as-is, plus 8 new ones — the original signed
payload merged with SageMaker's decision, Lambda's own fault classification, and an audit
trail (`signature_valid`, `replay_check`, `processed_at`). One easy-to-miss detail:
`q_values` is `json.dumps()`'d into a **string**, not kept as a nested array — worth knowing
if you ever query this data with Athena. This exact object is the S3 object body, at
`readings/year=2026/month=08/day=10/edge-device-002_1755008528114.json`.

### 8 — Device Shadow update (`lambda_function.py: update_thing_shadow()`)

```json
{
  "voltage_v": 362.4,
  "current_a": 78.6,
  "frequency_hz": 49.98,
  "temperature_c": 41.2,
  "power_factor": 0.947,
  "fault_type": "voltage_sag",
  "action": 2,
  "action_name": "switch_backup",
  "confidence": 0.9421,
  "signature_valid": true,
  "replay_check": "passed",
  "status": "processed",
  "processed_at": "2026-08-10T14:32:08.114562+00:00",
  "last_s3_object": "readings/year=2026/month=08/day=10/edge-device-002_1755008528114.json"
}
```
**Changed:** this is *not* the same 18-field record that went to S3 — Lambda builds a
separate, smaller object for the shadow. `seq`/`nonce`/`signature` (no longer needed once
verified) and `q_values` (too noisy for a "latest status" doc) are dropped;
`last_s3_object` is added as a pointer back to the full audit record.

### 9 — What the edge device actually receives

AWS IoT wraps stage 8 in its own shadow envelope before publishing to
`$aws/things/edge-device-002/shadow/update/accepted`, which the edge device is already
subscribed to:

```json
{
  "state": {
    "reported": { "...": "the 14-field object from stage 8" }
  },
  "metadata": {
    "reported": { "voltage_v": { "timestamp": 1755008528 }, "...": "..." }
  },
  "version": 47,
  "timestamp": 1755008528
}
```
`on_shadow_delta()` in `publish_to_iot.py` unwraps `state.reported`, prints the
`RECEIVED FROM CLOUD` block (`action`, `action_name`, `confidence`, and the echoed sensor
values), and writes it to `latest_prediction.json` — closing the loop from a single sensor
reading to a decision back at the edge, in under a second.

## Services used

| Service | Role |
|---|---|
| IoT Core | receive MQTT, route to Lambda |
| Lambda | orchestrate - runs in VPC private subnet |
| SageMaker | DQN RL inference endpoint |
| S3 | permanent data lake |
| SNS | alert emails |
| Secrets Manager | HMAC secret (never in code) |
| CloudWatch | logs, alarms, dashboard |
| VPC | Lambda isolated in private subnet with NAT |

## Folder structure

```
grid-iot-rl/
├── infrastructure/          ← Terraform - deploy AWS once
│   ├── main.tf              ← VPC + all modules
│   ├── variables.tf
│   ├── outputs.tf
│   ├── versions.tf
│   ├── terraform.tfvars.example
│   ├── lambda/
│   │   └── src/
│   │       └── lambda_function.py   ← deployed by Terraform
│   └── modules/
│       ├── iot/             ← Things + certs + policy
│       ├── lambda/          ← function + VPC config
│       ├── iam/             ← all IAM roles (Lambda, SageMaker, IoT rule)
│       ├── storage/         ← S3 bucket
│       ├── alerting/        ← SNS topic
│       ├── monitoring/      ← CloudWatch alarms + dashboard
│       └── secrets/         ← Secrets Manager
│
├── edge_simulator/
│   ├── local/               ← run on laptop
│   │   ├── publish_to_iot.py
│   │   ├── data_generator.py
│   │   ├── requirements.txt
│   │   ├── certs/           ← copy from ../certs/ after terraform apply
│   │   └── modules/
│   │       ├── noise_injector.py
│   │       ├── network_impairments.py
│   │       └── attack_simulator.py
│   └── colab/               ← upload to Google Drive, run in Colab
│       ├── multi_device_simulator.ipynb
│       ├── publish_to_iot.py
│       ├── data_generator.py
│       ├── requirements.txt
│       ├── certs/           ← copy cert files here before uploading to Drive
│       └── modules/
│
├── rl_model/
│   └── grid_voltage_notebook.ipynb  ← train + deploy RL agent
│
├── certs/                   ← auto-generated by Terraform (gitignored)
│   ├── root-CA.crt
│   ├── edge-device-001/
│   ├── edge-device-002/
│   └── edge-device-003/
│
└── docs/
    ├── test_scenarios.md
    ├── architecture-diagram.png
    └── Grid_IoT_RL_Presentation.pptx
```

---

## Setup - 5 steps

### Step 1 - Prerequisites

**What you need on your machine:**

| Requirement | Why |
|---|---|
| An AWS account with a payment method on file | this deploys real, billed resources — see *Cost* below |
| An IAM Access Key ID + Secret Access Key | for a user/role that can create VPC, IoT, Lambda, IAM, S3, SNS, SageMaker, Secrets Manager, and CloudWatch resources. `AdministratorAccess` is the simplest path for trying this out; scope it down for anything beyond evaluation |
| [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.5.0 | provisions all AWS infrastructure |
| [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) | used by Terraform and by the verification commands later in this doc |
| Python 3.11+ | generates the HMAC secret, runs the RL notebook, runs the edge simulator |
| Git | to clone this repo |

```bash
git clone https://github.com/hrshimpi/power-grid-iot-RL-pipeline.git
cd power-grid-iot-RL-pipeline

# install terraform
brew install terraform        # mac
# or: https://developer.hashicorp.com/terraform/install

# install AWS CLI and configure
aws configure
# enter: Access Key, Secret Key, region (us-east-1 -- see Region note below), json
```

**Region:** this project is built and tested against `us-east-1`. The RL notebook pulls a
prebuilt PyTorch container image from AWS's own SageMaker registry, and that registry's
account ID differs per region (`763104351884` for `us-east-1`) — currently hardcoded in
`infrastructure/modules/iam/main.tf` and in the notebook's `IMAGE` variable. Deploying to a
different region means finding [the correct registry account for that region](https://docs.aws.amazon.com/sagemaker/latest/dg/notebooks-available-images.html)
and updating both places; sticking with `us-east-1` avoids that entirely.

**Cost:** this isn't free-tier-only. Roughly: NAT Gateway ~$0.045/hr + data processing,
SageMaker endpoint ~$0.065/hr while it's deployed. Everything else (Lambda, IoT Core, S3,
SNS, Secrets Manager, CloudWatch) is negligible at this scale. Delete the SageMaker
endpoint and run `terraform destroy` when you're done — see *Clean up* below.

**Deploying under your own AWS account:** the code has no hardcoded account IDs or
personal values — it's designed to be cloned and deployed fresh by anyone. The one thing
that can collide: S3 bucket names are unique *globally*, not just within your account. If
`terraform apply` fails with a bucket-name-already-exists error, change `project` or `env`
in `terraform.tfvars` to get a different bucket name and re-apply.

### Step 2 - Deploy AWS infrastructure

```bash
cd infrastructure

# configure
cp terraform.tfvars.example terraform.tfvars

# generate the shared HMAC secret (edge devices <-> Lambda payload signing)
python -c "import secrets; print(secrets.token_hex(32))"

nano terraform.tfvars
# set: alert_email, hmac_secret (paste the value generated above), aws_region

# deploy (creates VPC, IoT, Lambda, S3, SNS, Secrets Manager, certs)
terraform init
terraform apply
# type: yes
# takes ~3 minutes
```

After apply, note the outputs:
```
iot_endpoint   = "xxx-ats.iot.us-east-1.amazonaws.com"
s3_bucket      = "grid-iot-rl-dev-data-lake"
lambda_function = "grid-iot-rl-dev-inference"
```

Cert files are auto-generated in `certs/` folder.

### Step 3 - Copy certs to edge simulators

```bash
# for local run
cp -r certs/* edge_simulator/local/certs/

# for Colab run
cp -r certs/* edge_simulator/colab/certs/
```

### Step 4 - Train and deploy RL model

```bash
# open in Jupyter or SageMaker Studio
open rl_model/grid_voltage_notebook.ipynb
# run all cells - takes ~15 minutes
# endpoint: grid-voltage-rl-v1
```

The notebook derives the S3 bucket, SageMaker execution role, and endpoint name from the
same `project`/`env` prefix Terraform uses (`grid-iot-rl-dev-*` by default), so they can't
drift out of sync with what `terraform apply` created. If your `terraform.tfvars` uses
non-default `project`/`env` values, override before running the notebook:

```bash
export GRID_PROJECT="grid-iot-rl"     # must match terraform.tfvars: project
export GRID_ENV="dev"                 # must match terraform.tfvars: env
export AWS_REGION="us-east-1"
# S3_BUCKET, SAGEMAKER_ENDPOINT, SAGEMAKER_ROLE_ARN, AWS_ACCOUNT_ID can each be
# overridden individually too - see Cell 8b in the notebook
```

### Step 5 - Run edge simulator

**Option A - local laptop:**
```bash
cd edge_simulator/local
pip install -r requirements.txt

# configure once - copy the template and fill in IOT_ENDPOINT / HMAC_SECRET
cp .env.example .env

# --client-id picks which provisioned device you're publishing as
# (edge-device-001 / 002 / 003) - a per-run flag, not part of .env
python publish_to_iot.py --client-id edge-device-001 --count 10 --mode happy
```

**Option B - Google Colab (3 devices simultaneously):**
1. Copy certs into `edge_simulator/colab/certs/` subfolders
2. Upload `edge_simulator/colab/` folder to Google Drive
3. Open `multi_device_simulator.ipynb` in Colab
4. Set `IOT_ENDPOINT` and `DRIVE_FOLDER` in Cell 3
5. Run all cells

---

## Test scenarios

```bash
# normal
python publish_to_iot.py --count 5 --mode happy

# faults
python publish_to_iot.py --count 5 --inject voltage_sag
python publish_to_iot.py --count 5 --inject overcurrent
python publish_to_iot.py --count 5 --inject frequency_drop
python publish_to_iot.py --count 5 --inject multi_fault

# network impairments
python publish_to_iot.py --count 10 --loss 0.10
python publish_to_iot.py --count 5  --latency 3.0

# security attacks
python publish_to_iot.py --count 5 --replay
python publish_to_iot.py --count 5 --mode attack

# chaos
python publish_to_iot.py --count 20 --mode chaos --fault-rate 0.2
```

---

## Generating the demo chart

`rl_model/grid_voltage_notebook.ipynb` includes a cell that plots one full voltage timeline
— normal operation, a fault, and the RL agent's recovery — from `demo_sag.json`. That file
is real, recorded pipeline output, not synthetic data: three separate
`publish_to_iot.py --save` runs, captured in order and merged into one file, so the chart is
literally three genuine round trips through the live pipeline stitched together.

```bash
cd edge_simulator/local

# 20 normal readings
python publish_to_iot.py --count 20 --mode happy --save part1_normal.json

# 20 voltage sag readings
python publish_to_iot.py --count 20 --inject voltage_sag --inject-duration 20 --save part2_fault.json

# 20 normal readings again
python publish_to_iot.py --count 20 --mode happy --save part3_recovery.json

# merge the three phases into one file, in order
python -c "
import json
parts = ['part1_normal.json', 'part2_fault.json', 'part3_recovery.json']
merged = []
for p in parts:
    with open(p) as f:
        merged.extend(json.load(f))
with open('demo_sag.json', 'w') as f:
    json.dump(merged, f, indent=2)
print(f'merged {len(merged)} readings into demo_sag.json')
"
```

Then open the notebook and run **Cell 14** (just above the chart cell — see the instructions
there too) followed by the chart cell itself. No need to move `demo_sag.json` anywhere: the
chart cell already checks `edge_simulator/local/demo_sag.json` as one of its search paths
(alongside a copy kept at `rl_model/demo_sag.json`), so a freshly captured file is picked up
automatically. The chart is saved to `rl_model/chart_voltage_timeline.png`.

The three-phase split matters — a single `--save` run with `--inject` from the start
produces a fault-only file with no "before" segment to contrast against, which is a much
less convincing chart than a clear normal → fault → recovery arc.

---

## Verify pipeline

```bash
# Lambda logs
aws logs tail /aws/lambda/grid-iot-rl-dev-inference --since 5m

# S3 data
aws s3 ls s3://grid-iot-rl-dev-data-lake/readings/ --recursive | tail -10
```

---

## Clean up

```bash
# delete SageMaker endpoint (costs $0.065/hr)
aws sagemaker delete-endpoint --endpoint-name grid-voltage-rl-v1

# destroy all AWS infrastructure
cd infrastructure
terraform destroy
```
