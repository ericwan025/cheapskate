"""Worker service.

Two concurrent parts share one process:

  1. A background thread that reserves jobs from the reliable queue and
     "processes" them (random sleep), in an interrupt-aware way.
  2. A FastAPI control server exposing POST /interrupt — the local stand-in for
     AWS's 2-minute spot-interruption warning. When hit, the worker stops taking
     new jobs, requeues its in-flight job (if any), and shuts the process down
     cleanly. GET /health reports current state.

Run:  python -m cheapskate.worker
"""
from __future__ import annotations

import logging
import os
import random
import signal
import socket
import threading
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from . import config
from .queue import JobQueue

WORKER_ID = os.environ.get("WORKER_ID") or socket.gethostname()

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [worker {WORKER_ID}] %(message)s",
)
log = logging.getLogger("worker")


class Worker:
    def __init__(self) -> None:
        self.queue = JobQueue(WORKER_ID)
        self.interrupt = threading.Event()   # set by POST /interrupt
        self.stopped = threading.Event()      # set when the loop has fully exited
        self.current_job: dict | None = None

    # --- the work loop ---------------------------------------------------
    def run(self) -> None:
        recovered = self.queue.requeue_orphans()
        if recovered:
            log.info("recovered %d orphaned job(s) from a previous run", recovered)

        while not self.interrupt.is_set():
            job = self.queue.reserve()   # blocks briefly, then re-checks interrupt
            if job is None:
                continue
            self.current_job = job
            self._process(job)
            self.current_job = None

        # We were told to drain. If we still hold a job, give it back.
        if self.current_job is not None:
            log.info("interrupted mid-job %s — requeueing", self.current_job["id"])
            self.queue.requeue(self.current_job)
            self.current_job = None

        log.info("drained cleanly, shutting down")
        self.stopped.set()

    def _process(self, job: dict) -> None:
        duration = random.uniform(config.JOB_MIN_SECONDS, config.JOB_MAX_SECONDS)
        log.info("START job %s (will take %.1fs)", job["id"], duration)

        elapsed = 0.0
        while elapsed < duration:
            if self.interrupt.is_set():
                # Abandon: hand the job back so no work is lost. run() requeues.
                log.info("START->ABANDON job %s at %.1fs (interrupt)", job["id"], elapsed)
                self.queue.requeue(job)
                self.current_job = None
                return
            time.sleep(config.WORK_TICK_SECONDS)
            elapsed += config.WORK_TICK_SECONDS

        self.queue.complete(job)
        log.info("DONE job %s", job["id"])


worker = Worker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    t = threading.Thread(target=worker.run, name="work-loop", daemon=True)
    t.start()

    def _watch_for_stop() -> None:
        # When the work loop finishes draining, stop the HTTP server too so the
        # container exits (0) instead of lingering.
        worker.stopped.wait()
        time.sleep(0.2)  # let the /interrupt response flush
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_watch_for_stop, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/interrupt")
def interrupt():
    """Local stand-in for the AWS spot 2-minute warning."""
    if not worker.interrupt.is_set():
        log.info("received INTERRUPT (2-minute warning) — will stop taking new jobs")
        worker.interrupt.set()
    return {"status": "draining", "worker_id": WORKER_ID}


@app.get("/health")
def health():
    return {
        "worker_id": WORKER_ID,
        "draining": worker.interrupt.is_set(),
        "current_job": worker.current_job["id"] if worker.current_job else None,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.WORKER_PORT, log_level="warning")
