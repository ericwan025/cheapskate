"""Central configuration, read from environment variables.

Nothing account-specific is hardcoded — every knob has an env override so the
same image behaves differently per service via docker-compose `environment:`.
"""
import os


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


# --- Redis / queue -------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Keys for the reliable-queue pattern.
PENDING_KEY = os.environ.get("PENDING_KEY", "jobs:pending")
# Per-worker in-flight list is PROCESSING_PREFIX + worker_id.
PROCESSING_PREFIX = os.environ.get("PROCESSING_PREFIX", "jobs:processing:")
# Set of completed job ids (dedup) + a raw completion counter (dup detection).
COMPLETED_SET_KEY = os.environ.get("COMPLETED_SET_KEY", "jobs:completed")
COMPLETED_COUNT_KEY = os.environ.get("COMPLETED_COUNT_KEY", "jobs:completed_count")

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

# --- Orchestrator --------------------------------------------------------
JOBS_PER_WORKER = _int("JOBS_PER_WORKER", 5)
MIN_WORKERS = _int("MIN_WORKERS", 1)
MAX_WORKERS = _int("MAX_WORKERS", 10)
ORCHESTRATOR_INTERVAL_SECONDS = _float("ORCHESTRATOR_INTERVAL_SECONDS", 3.0)
