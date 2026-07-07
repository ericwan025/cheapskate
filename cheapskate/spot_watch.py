"""EC2 spot-interruption watcher (Phase 3).

The real-AWS analog of the local POST /interrupt. AWS gives a spot instance a
~2-minute warning before reclaiming it by publishing a document at the instance
metadata "instance-action" endpoint. We poll it (IMDSv2, token-authenticated);
the moment it returns 200, we trip the same interrupt flag the local interrupter
sets — so the worker drains and requeues its in-flight job identically, whether
the "2-minute warning" came from our chaos tool or from AWS itself.

Runs as a daemon thread; a no-op unless QUEUE_BACKEND=sqs (i.e. on the real fleet).
"""
from __future__ import annotations

import logging
import threading

import httpx

from . import config

log = logging.getLogger("spot")

_TOKEN_URL = f"{config.IMDS_BASE}/latest/api/token"
_ACTION_URL = f"{config.IMDS_BASE}{config.SPOT_ACTION_PATH}"


def _get_token() -> str | None:
    try:
        resp = httpx.put(
            _TOKEN_URL,
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
            timeout=2.0,
        )
        if resp.status_code == 200:
            return resp.text
    except Exception:  # noqa: BLE001 - metadata unreachable (e.g. running locally)
        pass
    return None


def _interruption_scheduled() -> bool:
    """True once AWS has posted the spot instance-action (the 2-minute warning)."""
    token = _get_token()
    headers = {"X-aws-ec2-metadata-token": token} if token else {}
    try:
        resp = httpx.get(_ACTION_URL, headers=headers, timeout=2.0)
    except Exception:  # noqa: BLE001
        return False
    # 404 is the normal "no interruption" case; 200 carries {action, time}.
    return resp.status_code == 200


def watch(on_interrupt, stop_event: threading.Event) -> None:
    """Poll until an interruption is seen (then call on_interrupt) or we're stopped."""
    log.info("spot watcher started — polling %s every %.1fs", _ACTION_URL, config.SPOT_POLL_SECONDS)
    while not stop_event.wait(config.SPOT_POLL_SECONDS):
        if _interruption_scheduled():
            log.info("SPOT INTERRUPTION notice received — draining")
            on_interrupt()
            return


def start(on_interrupt, stop_event: threading.Event) -> threading.Thread | None:
    """Start the watcher thread if we're on the real (SQS) fleet; else do nothing."""
    if config.QUEUE_BACKEND != "sqs":
        return None
    t = threading.Thread(
        target=watch, args=(on_interrupt, stop_event), name="spot-watch", daemon=True
    )
    t.start()
    return t
