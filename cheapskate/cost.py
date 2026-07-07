"""Running cost accounting (Phase 5).

The whole point of cheapskate is spending less by leaning on cheap spot capacity.
This module makes that saving concrete: it integrates two dollar figures over the
lifetime of the run —

  actual        what the real (spot-heavy) fleet is costing, and
  hypothetical  what the *same* fleet would have cost billed 100% on-demand.

The orchestrator calls `accumulate()` once per control cycle. Each call adds
(rate * elapsed_hours) to both running totals, where the rates come from the live
fleet: actual uses each worker's real per-bucket price, hypothetical prices every
worker at the on-demand rate. The dashboard reads `totals()` and shows the gap —
the money the spot mix has saved so far.

Totals live in Redis so they survive across orchestrator restarts and are readable
by the separate dashboard process. Best-effort: if Redis is unreachable, both
accumulate() and totals() degrade quietly rather than breaking their callers.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import redis

from . import config, fleet

log = logging.getLogger("cost")

_client: Optional[redis.Redis] = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
    return _client


def rates(counts: dict[str, int]) -> tuple[float, float]:
    """(actual_$/hr, hypothetical_$/hr) for the current fleet.

    actual = real per-bucket prices; hypothetical = every worker at on-demand.
    """
    actual = fleet.cost_per_hour(counts)
    total = sum(counts.values())
    hypothetical = round(total * config.ON_DEMAND_HOURLY_COST, 4)
    return actual, hypothetical


def accumulate(counts: dict[str, int]) -> None:
    """Advance both running totals by the cost accrued since the last call."""
    now = time.time()
    try:
        client = _redis()
        last = client.get(config.COST_TS_KEY)
        client.set(config.COST_TS_KEY, now)
        if last is None:
            return  # first sample only establishes the clock; no interval yet.
        dt_hours = (now - float(last)) / 3600.0
        if dt_hours <= 0:
            return
        actual_rate, hypo_rate = rates(counts)
        client.incrbyfloat(config.COST_ACTUAL_KEY, actual_rate * dt_hours)
        client.incrbyfloat(config.COST_HYPO_KEY, hypo_rate * dt_hours)
    except Exception as exc:  # noqa: BLE001 - accounting must never break the loop
        log.warning("cost accumulate failed: %s", exc)


def totals() -> dict:
    """Accrued actual vs. hypothetical dollars, and the saving between them."""
    try:
        client = _redis()
        actual = float(client.get(config.COST_ACTUAL_KEY) or 0.0)
        hypothetical = float(client.get(config.COST_HYPO_KEY) or 0.0)
    except Exception as exc:  # noqa: BLE001
        log.warning("cost totals failed: %s", exc)
        actual = hypothetical = 0.0
    saved = hypothetical - actual
    saved_pct = round(saved / hypothetical * 100, 1) if hypothetical > 0 else 0.0
    return {
        "actual": round(actual, 6),
        "hypothetical": round(hypothetical, 6),
        "saved": round(saved, 6),
        "saved_pct": saved_pct,
    }
