"""Worker-fleet actuator (Phase 2).

The orchestrator decides *how many* workers it wants; this module makes it so.
It launches and terminates real worker containers via the Docker SDK, using the
same shared image the rest of the stack runs from.

Design notes:
  - Managed workers carry a label (MANAGED_LABEL=MANAGED_VALUE) so the scaler can
    find exactly the containers it owns and never touches anything else.
  - Launched workers get the network alias WORKER_SERVICE (e.g. "worker") so the
    existing interrupter/DNS discovery keeps working unchanged — getaddrinfo on
    that name returns every replica's IP.
  - Scaling down uses `stop(timeout=WORKER_STOP_TIMEOUT)`, which sends SIGTERM.
    The worker treats SIGTERM exactly like a spot interruption: it drains and
    requeues its in-flight job before exiting, so scale-down never loses work.

If Docker isn't reachable (no socket mounted, e.g. in unit tests), construction
raises; the orchestrator catches that and falls back to log-only mode.
"""
from __future__ import annotations

import logging
import socket
import uuid

import docker

from . import config

log = logging.getLogger("scaler")


class DockerScaler:
    def __init__(self) -> None:
        self.client = docker.from_env()
        # Fail fast if the daemon isn't actually reachable.
        self.client.ping()
        self.image = config.WORKER_IMAGE
        self.network = config.WORKER_NETWORK or self._detect_network()
        self.label_filter = f"{config.MANAGED_LABEL}={config.MANAGED_VALUE}"
        log.info(
            "docker scaler ready — image=%s network=%s", self.image, self.network
        )

    def _detect_network(self) -> str:
        """Join workers to the orchestrator's own network so `redis` resolves.

        The container hostname is its own id; inspecting ourselves reveals which
        compose network we're on.
        """
        me = self.client.containers.get(socket.gethostname())
        networks = list(me.attrs["NetworkSettings"]["Networks"].keys())
        if not networks:
            raise RuntimeError("orchestrator is not attached to any docker network")
        return networks[0]

    # --- introspection ---------------------------------------------------
    def list_workers(self) -> list:
        """All currently-running workers this orchestrator manages."""
        return self.client.containers.list(
            filters={"label": self.label_filter, "status": "running"}
        )

    def count(self) -> int:
        return len(self.list_workers())

    # --- actuation -------------------------------------------------------
    def scale_to(self, desired: int) -> None:
        current = self.list_workers()
        delta = desired - len(current)
        if delta > 0:
            for _ in range(delta):
                self._launch_one()
        elif delta < 0:
            # Stop the newest workers first (least likely to be deep into a job).
            victims = sorted(
                current, key=lambda c: c.attrs["Created"], reverse=True
            )[: -delta]
            for c in victims:
                self._stop_one(c)

    def _launch_one(self) -> None:
        name = f"cheapskate-worker-{uuid.uuid4().hex[:8]}"
        environment = {
            "REDIS_URL": config.REDIS_URL,
            "JOB_MIN_SECONDS": str(config.JOB_MIN_SECONDS),
            "JOB_MAX_SECONDS": str(config.JOB_MAX_SECONDS),
            "WORKER_PORT": str(config.WORKER_PORT),
        }
        labels = {config.MANAGED_LABEL: config.MANAGED_VALUE}

        # Attach to the compose network *with* the "worker" alias in one shot, via
        # the low-level API — the high-level run() can't set network aliases.
        networking = self.client.api.create_networking_config(
            {
                self.network: self.client.api.create_endpoint_config(
                    aliases=[config.WORKER_SERVICE]
                )
            }
        )
        resp = self.client.api.create_container(
            image=self.image,
            command=["python", "-m", "cheapskate.worker"],
            environment=environment,
            labels=labels,
            name=name,
            host_config=self.client.api.create_host_config(auto_remove=True),
            networking_config=networking,
        )
        self.client.api.start(resp["Id"])
        log.info("scaled UP  -> launched %s", name)

    def _stop_one(self, container) -> None:
        name = container.name
        try:
            # SIGTERM -> worker drains & requeues in-flight job -> exits 0.
            # auto_remove cleans the container up once it's stopped.
            container.stop(timeout=config.WORKER_STOP_TIMEOUT)
            log.info("scaled DOWN -> stopped %s (drained)", name)
        except docker.errors.NotFound:
            pass  # already gone
        except Exception as exc:  # noqa: BLE001 - best effort
            log.warning("failed to stop %s: %s", name, exc)

    def cleanup(self) -> None:
        """Stop every managed worker (called on orchestrator shutdown)."""
        workers = self.list_workers()
        if not workers:
            return
        log.info("cleanup — stopping %d managed worker(s)", len(workers))
        for c in workers:
            self._stop_one(c)
