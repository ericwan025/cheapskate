"""Lifecycle event log (Phase 4).

A small append-only feed of the things worth watching in a cost-aware autoscaler:
spot/interrupt notices, mid-job requeues, and orphan recoveries. Workers record
events here; the dashboard reads and displays them so an interruption and the
retry that follows are visible, not silent.

Stored as a capped Redis list (newest first, trimmed to EVENTS_MAX). Recording is
best-effort and only active in redis mode — an SQS worker on AWS can't reach this
local Redis, so it simply skips logging rather than failing the job. Reads always
degrade to an empty feed if Redis is unreachable, so the dashboard never breaks.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import redis

from . import config

log = logging.getLogger("events")

# Event type constants (kept stable so the dashboard can style them).
INTERRUPT = "interrupt"          # 2-minute warning / SIGTERM: worker will drain
REQUEUE = "requeue"              # in-flight job handed back so it isn't lost
ORPHAN_RECOVERED = "orphan"      # stranded job recovered from a dead worker

_client: Optional[redis.Redis] = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
    return _client


def record(event_type: str, worker_id: str, job_id=None, detail: str = "") -> None:
    """Append an event to the feed. Best-effort; never raises into the caller."""
    if config.QUEUE_BACKEND != "redis":
        return  # AWS workers can't reach the local Redis; skip silently.
    entry = {
        "ts": int(time.time()),
        "type": event_type,
        "worker_id": worker_id,
        "job_id": job_id,
        "detail": detail,
    }
    try:
        pipe = _redis().pipeline()
        pipe.lpush(config.EVENTS_KEY, json.dumps(entry))
        pipe.ltrim(config.EVENTS_KEY, 0, config.EVENTS_MAX - 1)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001 - logging must not break the worker
        log.warning("failed to record event %s: %s", event_type, exc)


def recent(limit: int = 25) -> list[dict]:
    """Most-recent events, newest first. Empty list if Redis is unreachable."""
    try:
        raw = _redis().lrange(config.EVENTS_KEY, 0, limit - 1)
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to read events: %s", exc)
        return []
    return [json.loads(r) for r in raw]
