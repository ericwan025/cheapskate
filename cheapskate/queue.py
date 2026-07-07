"""Reliable Redis job queue.

Guarantees each job is *completed exactly once* even if workers are interrupted
mid-job, using the classic reliable-queue pattern:

    pending list  --BRPOPLPUSH-->  per-worker processing list
                                          |
                            complete: LREM from processing + record completion
                            interrupt: LPUSH back to pending + LREM from processing

A job only leaves the system (is forgotten) after it has been recorded complete.
If a worker dies while a job sits in its processing list, `requeue_orphans`
moves it back to pending so it is never lost.

Job payloads are JSON objects; the minimal shape is {"id": <int>}.
"""
from __future__ import annotations

import json
from typing import Optional

import redis

from . import config


class JobQueue:
    def __init__(self, worker_id: str, redis_url: str = config.REDIS_URL):
        self.worker_id = worker_id
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.pending_key = config.PENDING_KEY
        self.processing_key = f"{config.PROCESSING_PREFIX}{worker_id}"

    # --- producer side ---------------------------------------------------
    def push(self, job: dict) -> None:
        """Add a job to the head of the pending list (producer side)."""
        self.client.lpush(self.pending_key, json.dumps(job))

    def pending_depth(self) -> int:
        return self.client.llen(self.pending_key)

    # --- worker side -----------------------------------------------------
    def reserve(self, block_seconds: int = config.QUEUE_BLOCK_SECONDS) -> Optional[dict]:
        """Atomically move one job pending -> this worker's processing list.

        Blocks up to `block_seconds`; returns None on timeout so the caller can
        re-check its interrupt flag. The job is now "in flight" and is not lost
        even if this process dies — it can be recovered from the processing list.
        """
        raw = self.client.brpoplpush(self.pending_key, self.processing_key, timeout=block_seconds)
        if raw is None:
            return None
        return json.loads(raw)

    def complete(self, job: dict) -> None:
        """Mark a reserved job done: record completion, then drop it in-flight.

        Uses a SET (dedup) + counter (dup detection) atomically so the test can
        prove exactly-once: len(set) == counter == number of jobs.
        """
        raw = json.dumps(job)
        pipe = self.client.pipeline()
        pipe.sadd(config.COMPLETED_SET_KEY, job["id"])
        pipe.incr(config.COMPLETED_COUNT_KEY)
        pipe.lrem(self.processing_key, 1, raw)
        pipe.execute()

    def requeue(self, job: dict) -> None:
        """Abandon a reserved-but-unfinished job: put it back for someone else."""
        raw = json.dumps(job)
        pipe = self.client.pipeline()
        pipe.lpush(self.pending_key, raw)
        pipe.lrem(self.processing_key, 1, raw)
        pipe.execute()

    def requeue_orphans(self) -> int:
        """Recover any jobs stranded in *this worker's* processing list.

        Called on startup (in case a previous incarnation with the same id died
        hard). Returns the number of jobs recovered.
        """
        recovered = 0
        while True:
            raw = self.client.rpoplpush(self.processing_key, self.pending_key)
            if raw is None:
                break
            recovered += 1
        return recovered

    # --- introspection (used by tests / dashboard later) -----------------
    def completed_count(self) -> int:
        return int(self.client.get(config.COMPLETED_COUNT_KEY) or 0)

    def completed_ids(self) -> set[int]:
        return {int(x) for x in self.client.smembers(config.COMPLETED_SET_KEY)}
