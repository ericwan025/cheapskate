"""Interrupter — the local stand-in for a random AWS spot interruption.

Picks one running worker at random and sends it POST /interrupt, i.e. the
"2-minute warning". The chosen worker then stops taking new jobs, requeues its
in-flight job, and shuts down cleanly — exactly the behaviour we'll later
trigger from the EC2 spot metadata endpoint.

Worker discovery: in docker-compose the service name (WORKER_SERVICE) resolves
via DNS to the IPs of *all* replicas. We resolve it, pick one, and hit it.

Run:  python -m cheapskate.interrupter                  # interrupt 1 random worker
      python -m cheapskate.interrupter 3                # interrupt 3 random workers
      INTERRUPT_COUNT=3 python -m cheapskate.interrupter # same, via env (compose)
      python -m cheapskate.interrupter http://host:8000 # interrupt a specific worker
"""
from __future__ import annotations

import logging
import os
import random
import socket
import sys

import httpx

from . import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [interrupter] %(message)s")
log = logging.getLogger("interrupter")


def discover_workers() -> list[str]:
    """Resolve the worker service name to the base URLs of all live replicas."""
    infos = socket.getaddrinfo(config.WORKER_SERVICE, config.WORKER_PORT, proto=socket.IPPROTO_TCP)
    ips = sorted({info[4][0] for info in infos})
    return [f"http://{ip}:{config.WORKER_PORT}" for ip in ips]


def interrupt_one(base_url: str) -> None:
    try:
        resp = httpx.post(f"{base_url}/interrupt", timeout=5.0)
        resp.raise_for_status()
        body = resp.json()
        log.info("interrupted worker %s at %s -> %s", body.get("worker_id"), base_url, body.get("status"))
    except Exception as exc:  # noqa: BLE001 - best-effort chaos tool
        log.warning("failed to interrupt %s: %s", base_url, exc)


def run(count: int) -> None:
    workers = discover_workers()
    if not workers:
        log.warning("no workers found for service '%s'", config.WORKER_SERVICE)
        return

    targets = random.sample(workers, k=min(count, len(workers)))
    log.info("found %d worker(s); interrupting %d", len(workers), len(targets))
    for base_url in targets:
        interrupt_one(base_url)


def _parse_args(argv: list[str]) -> tuple[list[str] | None, int]:
    """Return (explicit_urls, count). Explicit URL(s) bypass discovery.

    Count comes from a positional arg when given, else the INTERRUPT_COUNT env
    var (default 1). Env is how docker-compose drives this — `docker compose run`
    replaces the command with any trailing token, so positional args can't be
    used there.
    """
    if len(argv) > 1 and argv[1].startswith("http"):
        return argv[1:], len(argv) - 1
    if len(argv) > 1:
        return None, int(argv[1])
    return None, int(os.environ.get("INTERRUPT_COUNT", "1"))


if __name__ == "__main__":
    urls, n = _parse_args(sys.argv)
    if urls is not None:
        for u in urls:
            interrupt_one(u)
    else:
        run(n)
