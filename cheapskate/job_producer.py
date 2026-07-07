"""Job producer.

Pushes N jobs with incrementing integer IDs onto the reliable queue's pending
list. In Phase 1 this is how work enters the system; later phases replace the
source (SQS) but keep the same {"id": <int>} job shape.

Run:  python -m cheapskate.job_producer            # pushes JOB_COUNT jobs
      python -m cheapskate.job_producer 50         # pushes 50 jobs
      JOB_COUNT=200 python -m cheapskate.job_producer
"""
from __future__ import annotations

import logging
import os
import sys

from . import config
from .queue import JobQueue

logging.basicConfig(level=logging.INFO, format="%(asctime)s [producer] %(message)s")
log = logging.getLogger("producer")


def produce(count: int, start_id: int = 1) -> None:
    # The producer never reserves jobs, so its worker_id is irrelevant; it only
    # touches the shared pending list.
    queue = JobQueue(worker_id="producer")

    log.info("pushing %d job(s) starting at id %d", count, start_id)
    for job_id in range(start_id, start_id + count):
        queue.push({"id": job_id})
    log.info("done — pending depth is now %d", queue.pending_depth())


def _resolve_count(argv: list[str]) -> int:
    if len(argv) > 1:
        return int(argv[1])
    return int(os.environ.get("JOB_COUNT", config.DEFAULT_JOB_COUNT))


if __name__ == "__main__":
    produce(_resolve_count(sys.argv))
