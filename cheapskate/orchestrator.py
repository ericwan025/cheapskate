"""Orchestrator (Phase 1: decide + log only).

Polls the queue depth on a fixed interval and computes how many workers it
*would* want: one worker per JOBS_PER_WORKER pending jobs, clamped to
[MIN_WORKERS, MAX_WORKERS]. In Phase 1 it only LOGS the decision — it does not
start or stop any containers yet (that's Phase 2). Later phases keep this same
decision function and swap the actuator (Docker SDK, then the AWS Auto Scaling
API).

Run:  python -m cheapskate.orchestrator
"""
from __future__ import annotations

import logging
import math
import time

from . import config
from .queue import JobQueue

logging.basicConfig(level=logging.INFO, format="%(asctime)s [orchestrator] %(message)s")
log = logging.getLogger("orchestrator")


def desired_workers(pending: int) -> int:
    """One worker per JOBS_PER_WORKER pending jobs, clamped to [MIN, MAX]."""
    raw = math.ceil(pending / config.JOBS_PER_WORKER)
    return max(config.MIN_WORKERS, min(config.MAX_WORKERS, raw))


def run() -> None:
    queue = JobQueue(worker_id="orchestrator")
    log.info(
        "starting — 1 worker per %d jobs, clamp [%d, %d], polling every %.1fs",
        config.JOBS_PER_WORKER,
        config.MIN_WORKERS,
        config.MAX_WORKERS,
        config.ORCHESTRATOR_INTERVAL_SECONDS,
    )

    last_decision: int | None = None
    while True:
        pending = queue.pending_depth()
        completed = queue.completed_count()
        want = desired_workers(pending)

        change = "" if want == last_decision else f"  (was {last_decision})"
        log.info(
            "pending=%d completed=%d -> want %d worker(s)%s", pending, completed, want, change
        )
        last_decision = want

        time.sleep(config.ORCHESTRATOR_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
