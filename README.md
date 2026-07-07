# cheapskate

A cost-aware autoscaler for an AWS EC2 worker fleet. It scales workers on queue
depth, mixes cheap **spot** capacity with reliable **on-demand** capacity, and
handles AWS's 2-minute spot-interruption warning gracefully — draining and
requeuing in-flight jobs so **no work is ever lost** — while tracking how much
the spot mix saves versus running everything on-demand.

The same code runs two ways, selected by one env var (`QUEUE_BACKEND`):

| | Local (`redis`) | AWS (`sqs`) |
|---|---|---|
| Job queue | reliable Redis list | SQS (+ DLQ) |
| Fleet actuator | Docker containers | spot + on-demand Auto Scaling Groups |
| "2-min warning" | `POST /interrupt` chaos tool | real EC2 spot metadata notice |

## How it works

- **Reliable queue** — a job only leaves the system after it's recorded complete.
  A worker reserves a job (it goes in-flight, not deleted); on interruption it's
  handed back. Redis uses the `BRPOPLPUSH` processing-list pattern; SQS uses
  visibility timeouts. Either way: at-least-once, never lost.
- **Graceful interruption** — a spot notice, a `SIGTERM` scale-down, and the local
  `/interrupt` tool all trip the *same* drain path: stop taking new work, requeue
  the current job, exit cleanly.
- **Orchestrator** — polls queue depth, wants `ceil(pending / JOBS_PER_WORKER)`
  workers (clamped), and actuates. On AWS it also splits that total across spot vs
  on-demand: a reliability baseline always rides on-demand, and an
  interruption-pressure signal (EWMA of the spot ASG's capacity shortfall) shifts
  more onto on-demand when spot is actually being reclaimed.
- **Dashboard** — live queue/fleet/cost, an interruption & retry event log, and a
  running **actual vs 100%-on-demand** cost comparison showing the savings.

## Run locally (Docker)

```bash
docker compose up --build redis orchestrator dashboard   # stack + UI at :8080
docker compose run --rm --no-deps -e JOB_COUNT=50 producer          # push 50 jobs
docker compose run --rm --no-deps -e INTERRUPT_COUNT=1 interrupter   # chaos: interrupt a worker
```

Open <http://localhost:8080> to watch it scale, drain interruptions, and accrue
savings. The orchestrator owns the worker fleet — it launches/kills worker
containers itself, so there's no `--scale`.

## Tests

```bash
tests/phase1_smoke.sh    # exactly-once, no-loss under interruptions
tests/phase2_scale.sh    # orchestrator scales the fleet up and back down
python -m pytest tests/test_no_loss.py
```

## Deploy to AWS

Infrastructure is Terraform in [`infra/`](infra/) — SQS + DLQ, a least-privilege
IAM role/instance profile, an ECR repo, a launch template, and two ASGs (100%
spot + on-demand) sharing it. Nothing account-specific is hardcoded; everything
is a variable and credentials come from your configured AWS profile/env.

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # set region, etc.
terraform init && terraform apply
# build & push the worker image to the ECR repo from the output, then:
export QUEUE_BACKEND=sqs SQS_QUEUE_URL=... SPOT_ASG_NAME=... ON_DEMAND_ASG_NAME=...
python -m cheapskate.orchestrator
```

Both ASGs default to `desired_capacity = 0`, so an un-driven stack costs nothing.
See [`infra/README.md`](infra/README.md) for details and the credential/cost notes.

## Configuration

All knobs are environment variables with sane defaults — see
[`cheapskate/config.py`](cheapskate/config.py). Common ones:

| var | default | meaning |
|---|---|---|
| `QUEUE_BACKEND` | `redis` | `redis` (local) or `sqs` (AWS) |
| `JOBS_PER_WORKER` | `5` | queue depth each worker is expected to absorb |
| `MIN_WORKERS` / `MAX_WORKERS` | `1` / `10` | fleet clamp |
| `ON_DEMAND_BASE_FRACTION` | `0.2` | fleet share always kept on-demand |
| `SPOT_HOURLY_COST` / `ON_DEMAND_HOURLY_COST` | `0.0063` / `0.0208` | prices for the cost estimate |

## Stack

Python (FastAPI), Redis, SQS, Docker + Docker Compose, Terraform, boto3.
