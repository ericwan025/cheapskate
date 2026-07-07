"""Central configuration, read from environment variables.

Nothing account-specific is hardcoded — every knob has an env override so the
same image behaves differently per service via docker-compose `environment:`.
"""
import os


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


# --- Queue backend -------------------------------------------------------
# "redis" (local/Phase 1-2) or "sqs" (AWS/Phase 3). Selects the JobQueue impl.
QUEUE_BACKEND = os.environ.get("QUEUE_BACKEND", "redis").lower()

# --- Redis / queue -------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Keys for the reliable-queue pattern.
PENDING_KEY = os.environ.get("PENDING_KEY", "jobs:pending")
# Per-worker in-flight list is PROCESSING_PREFIX + worker_id.
PROCESSING_PREFIX = os.environ.get("PROCESSING_PREFIX", "jobs:processing:")
# Set of completed job ids (dedup) + a raw completion counter (dup detection).
COMPLETED_SET_KEY = os.environ.get("COMPLETED_SET_KEY", "jobs:completed")
COMPLETED_COUNT_KEY = os.environ.get("COMPLETED_COUNT_KEY", "jobs:completed_count")
# Capped list of recent lifecycle events (interruptions, requeues, recoveries)
# that the dashboard displays. Newest first; trimmed to EVENTS_MAX entries.
EVENTS_KEY = os.environ.get("EVENTS_KEY", "events:log")
EVENTS_MAX = _int("EVENTS_MAX", 100)
# Running cost accounting (Phase 5): dollars accrued so far by the real fleet vs.
# the same workload run 100% on-demand, integrated over time by the orchestrator.
COST_ACTUAL_KEY = os.environ.get("COST_ACTUAL_KEY", "cost:actual")
COST_HYPO_KEY = os.environ.get("COST_HYPO_KEY", "cost:hypothetical")
COST_TS_KEY = os.environ.get("COST_TS_KEY", "cost:last_ts")

# --- Worker --------------------------------------------------------------
# Simulated job processing time is a random sleep in [MIN, MAX] seconds.
JOB_MIN_SECONDS = _float("JOB_MIN_SECONDS", 5.0)
JOB_MAX_SECONDS = _float("JOB_MAX_SECONDS", 30.0)
# How long a blocking queue read waits before looping to re-check the
# interrupt flag. Keeps shutdown responsive.
QUEUE_BLOCK_SECONDS = _int("QUEUE_BLOCK_SECONDS", 5)
# Granularity of the interruptible sleep while "processing" a job.
WORK_TICK_SECONDS = _float("WORK_TICK_SECONDS", 0.5)
# Port the worker's control HTTP server ("2-minute warning" door) listens on.
WORKER_PORT = _int("WORKER_PORT", 8000)

# --- Producer ------------------------------------------------------------
# Default number of jobs pushed when none is given on the CLI / env.
DEFAULT_JOB_COUNT = _int("JOB_COUNT", 50)

# --- SQS (Phase 3) -------------------------------------------------------
# URL of the job queue on AWS. boto3 reads AWS_REGION / AWS_DEFAULT_REGION and
# credentials from the environment / instance role — never hardcoded here.
SQS_QUEUE_URL = os.environ.get("SQS_QUEUE_URL", "")
AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""
# Long-poll wait when receiving (0-20s). Fewer empty receives, faster shutdown.
SQS_WAIT_SECONDS = _int("SQS_WAIT_SECONDS", 5)

# --- Spot interruption watcher (Phase 3) ---------------------------------
# The worker polls the EC2 instance-metadata "instance-action" endpoint; a 200
# there is the ~2-minute spot-termination warning. Off unless we're on SQS.
SPOT_POLL_SECONDS = _float("SPOT_POLL_SECONDS", 5.0)
IMDS_BASE = os.environ.get("IMDS_BASE", "http://169.254.169.254")
SPOT_ACTION_PATH = os.environ.get("SPOT_ACTION_PATH", "/latest/meta-data/spot/instance-action")

# --- Dashboard (Phase 4) -------------------------------------------------
# Port the read-only status dashboard serves on.
DASHBOARD_PORT = _int("DASHBOARD_PORT", 8080)
# How often (seconds) the dashboard page auto-refreshes its stats.
DASHBOARD_REFRESH_SECONDS = _int("DASHBOARD_REFRESH_SECONDS", 2)
# Approximate hourly $ cost per worker instance, used for the live burn-rate
# estimate. Spot is cheap-but-interruptible; on-demand is the reliable baseline.
# Defaults are ballpark t3.small us-east-1 figures — override per instance type.
SPOT_HOURLY_COST = _float("SPOT_HOURLY_COST", 0.0063)
ON_DEMAND_HOURLY_COST = _float("ON_DEMAND_HOURLY_COST", 0.0208)

# --- Interrupter ---------------------------------------------------------
# DNS name that resolves to all worker replicas (docker-compose service name).
# The interrupter picks one at random and sends it the "2-minute warning".
WORKER_SERVICE = os.environ.get("WORKER_SERVICE", "worker")

# --- Orchestrator --------------------------------------------------------
JOBS_PER_WORKER = _int("JOBS_PER_WORKER", 5)
MIN_WORKERS = _int("MIN_WORKERS", 1)
MAX_WORKERS = _int("MAX_WORKERS", 10)
ORCHESTRATOR_INTERVAL_SECONDS = _float("ORCHESTRATOR_INTERVAL_SECONDS", 3.0)

# --- AWS Auto Scaling actuator (Phase 3: orchestrator drives the ASGs) ---
# The two ASGs Terraform created: one 100% spot (cheap, interruptible), one
# on-demand (reliable fallback). Names come from `terraform output`.
SPOT_ASG_NAME = os.environ.get("SPOT_ASG_NAME", "")
ON_DEMAND_ASG_NAME = os.environ.get("ON_DEMAND_ASG_NAME", "")
# Hard caps on desired capacity per ASG — mirror the Terraform max_size so the
# orchestrator never asks for more than the ASG will allow (set_desired_capacity
# fails outside [min,max]).
SPOT_MAX_CAPACITY = _int("SPOT_MAX_CAPACITY", 10)
ON_DEMAND_MAX_CAPACITY = _int("ON_DEMAND_MAX_CAPACITY", 5)
# Reliability baseline: always keep at least this fraction of the fleet on
# on-demand so a spot mass-reclaim can't drop us to zero capacity.
ON_DEMAND_BASE_FRACTION = _float("ON_DEMAND_BASE_FRACTION", 0.2)
# Recent-interruption pressure is an EWMA of the spot ASG's capacity shortfall
# (desired minus actually-in-service). As it rises, more of the fleet is placed
# on on-demand. Alpha weights the newest sample.
INTERRUPTION_EWMA_ALPHA = _float("INTERRUPTION_EWMA_ALPHA", 0.3)

# --- Scaler (Phase 2: orchestrator actuates worker containers) -----------
# Image the orchestrator launches worker containers from (same shared image).
WORKER_IMAGE = os.environ.get("WORKER_IMAGE", "cheapskate:local")
# Docker network the launched workers join so they can reach redis by name.
# Empty => auto-detect the orchestrator's own network at runtime.
WORKER_NETWORK = os.environ.get("WORKER_NETWORK", "")
# Grace period (seconds) a worker gets to drain after SIGTERM before SIGKILL.
# Must exceed the worker's own drain wait so in-flight jobs are requeued.
WORKER_STOP_TIMEOUT = _int("WORKER_STOP_TIMEOUT", 15)
# Label used to find/own the workers this orchestrator manages.
MANAGED_LABEL = os.environ.get("MANAGED_LABEL", "cheapskate.managed-by")
MANAGED_VALUE = os.environ.get("MANAGED_VALUE", "orchestrator")
# Remove managed workers when the orchestrator itself shuts down, so a
# `compose down` / restart doesn't leave an orphaned fleet behind.
CLEANUP_ON_EXIT = os.environ.get("CLEANUP_ON_EXIT", "true").lower() == "true"
