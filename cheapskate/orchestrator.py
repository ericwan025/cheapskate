"""Orchestrator (Phase 2: decide + actuate).

Polls the queue depth on a fixed interval and computes how many workers it wants:
one worker per JOBS_PER_WORKER pending jobs, clamped to [MIN_WORKERS, MAX_WORKERS].

Phase 1 only logged that decision. Phase 2 enacts it: a DockerScaler launches and
terminates real worker containers to match. Scale-down is safe — stopping a worker
sends SIGTERM, which the worker treats like a spot interruption and drains, so no
in-flight job is ever lost.

If Docker isn't reachable (e.g. the socket isn't mounted), it degrades gracefully
to the Phase 1 log-only behaviour instead of crashing. Later phases keep this same
decision function and swap the actuator for the AWS Auto Scaling API.

Run:  python -m cheapskate.orchestrator
"""
from __future__ import annotations

import logging
import math
import signal
import time

from . import config
from .queue import JobQueue

logging.basicConfig(level=logging.INFO, format="%(asctime)s [orchestrator] %(message)s")
log = logging.getLogger("orchestrator")


def desired_workers(pending: int) -> int:
    """One worker per JOBS_PER_WORKER pending jobs, clamped to [MIN, MAX]."""
    raw = math.ceil(pending / config.JOBS_PER_WORKER)
    return max(config.MIN_WORKERS, min(config.MAX_WORKERS, raw))


def _make_scaler():
    """Return a DockerScaler, or None to fall back to log-only mode."""
    try:
        from .scaler import DockerScaler

        return DockerScaler()
    except Exception as exc:  # noqa: BLE001 - any docker/socket problem => log-only
        log.warning("docker unavailable (%s) — running log-only, no actuation", exc)
        return None


def run() -> None:
    queue = JobQueue(worker_id="orchestrator")
    scaler = _make_scaler()

    log.info(
        "starting — 1 worker per %d jobs, clamp [%d, %d], polling every %.1fs, mode=%s",
        config.JOBS_PER_WORKER,
        config.MIN_WORKERS,
        config.MAX_WORKERS,
        config.ORCHESTRATOR_INTERVAL_SECONDS,
        "actuate" if scaler else "log-only",
    )

    stopping = {"flag": False}

    def _handle_signal(signum, _frame):
        log.info("received signal %s — shutting down", signum)
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    last_decision: int | None = None
    try:
        while not stopping["flag"]:
            pending = queue.pending_depth()
            completed = queue.completed_count()
            want = desired_workers(pending)
            running = scaler.count() if scaler else None

            change = "" if want == last_decision else f"  (was {last_decision})"
            log.info(
                "pending=%d completed=%d running=%s -> want %d worker(s)%s",
                pending,
                completed,
                "?" if running is None else running,
                want,
                change,
            )

            if scaler is not None and running != want:
                scaler.scale_to(want)

            last_decision = want
            time.sleep(config.ORCHESTRATOR_INTERVAL_SECONDS)
    finally:
        if scaler is not None and config.CLEANUP_ON_EXIT:
            scaler.cleanup()


if __name__ == "__main__":
    run()
