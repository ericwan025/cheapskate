"""Phase 1 finale: prove no job is ever lost or double-completed.

Scenario (the whole point of cheapskate's graceful-interrupt design):

  1. Push 50 jobs.
  2. Run a fleet of worker threads pulling from the shared reliable queue.
  3. Randomly interrupt workers *mid-job* (the local "2-minute warning"). An
     interrupted worker requeues its in-flight job and exits; we immediately
     spawn a replacement, exactly as `restart: unless-stopped` / an ASG would.
  4. Keep going until 50 completions are recorded.

Correctness (exactly-once) holds iff:
      completed_count == 50  and  completed_ids == {1..50}
i.e. none lost (all 50 present) and none double-counted (count == set size).

Runs against a real Redis if REDIS_URL is reachable; otherwise falls back to
an in-process fakeredis so it works with no infrastructure. Uses the real
`Worker` and `JobQueue` code — only the timings are shrunk and the "restart"
is simulated in-process.

Run:  python -m tests.test_no_loss
      REDIS_URL=redis://localhost:6379/0 python -m tests.test_no_loss
"""
from __future__ import annotations

import os
import random
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cheapskate import config
from cheapskate.queue import JobQueue

NUM_JOBS = 50
INITIAL_WORKERS = 4
INTERRUPT_EVERY = (0.3, 0.8)   # seconds between chaos strikes
TIMEOUT_SECONDS = 60


# --- backend selection: real Redis if reachable, else fakeredis -----------
def make_client_factory():
    url = config.REDIS_URL
    try:
        import redis
        c = redis.Redis.from_url(url, decode_responses=True)
        c.ping()
        c.flushall()
        print(f"using real Redis at {url}")
        return lambda: redis.Redis.from_url(url, decode_responses=True)
    except Exception as exc:  # noqa: BLE001
        import fakeredis
        server = fakeredis.FakeServer()
        print(f"real Redis unavailable ({exc.__class__.__name__}); using fakeredis")
        return lambda: fakeredis.FakeStrictRedis(server=server, decode_responses=True)


CLIENT = make_client_factory()


def make_queue(worker_id: str) -> JobQueue:
    q = JobQueue.__new__(JobQueue)
    q.worker_id = worker_id
    q.client = CLIENT()
    q.pending_key = config.PENDING_KEY
    q.processing_key = f"{config.PROCESSING_PREFIX}{worker_id}"
    return q


def make_worker(worker_id: str):
    from cheapskate.worker import Worker
    w = Worker.__new__(Worker)
    w.queue = make_queue(worker_id)
    w.interrupt = threading.Event()
    w.stopped = threading.Event()
    w.current_job = None
    return w


def main() -> int:
    # Shrink processing time so the test finishes quickly but jobs still last
    # long enough to be caught mid-flight by the chaos thread.
    config.JOB_MIN_SECONDS = 0.3
    config.JOB_MAX_SECONDS = 1.2
    config.WORK_TICK_SECONDS = 0.05
    config.QUEUE_BLOCK_SECONDS = 1

    control = make_queue("test-control")
    for job_id in range(1, NUM_JOBS + 1):
        control.push({"id": job_id})
    print(f"pushed {NUM_JOBS} jobs; pending={control.pending_depth()}")

    workers: list = []
    workers_lock = threading.Lock()
    stop_chaos = threading.Event()
    interruptions = {"count": 0}

    def spawn(worker_id: str) -> None:
        w = make_worker(worker_id)
        t = threading.Thread(target=w.run, name=worker_id, daemon=True)
        with workers_lock:
            workers.append((w, t))
        t.start()

    for i in range(INITIAL_WORKERS):
        spawn(f"w{i}")

    # Chaos: repeatedly interrupt a random *live* worker and replace it.
    next_id = INITIAL_WORKERS

    def chaos() -> None:
        nonlocal next_id
        while not stop_chaos.is_set():
            time.sleep(random.uniform(*INTERRUPT_EVERY))
            with workers_lock:
                live = [(w, t) for (w, t) in workers if not w.stopped.is_set()]
            if not live:
                continue
            victim, _ = random.choice(live)
            if victim.interrupt.is_set():
                continue
            victim.interrupt.set()
            interruptions["count"] += 1
            spawn(f"w{next_id}")   # replacement, like an ASG bringing capacity back
            next_id += 1

    chaos_thread = threading.Thread(target=chaos, daemon=True)
    chaos_thread.start()

    # Wait until all jobs are completed (or timeout).
    deadline = time.time() + TIMEOUT_SECONDS
    while time.time() < deadline:
        if control.completed_count() >= NUM_JOBS and control.pending_depth() == 0:
            time.sleep(0.3)  # settle
            break
        time.sleep(0.1)

    stop_chaos.set()

    # Drain remaining live workers cleanly.
    with workers_lock:
        for w, _ in workers:
            w.interrupt.set()
    time.sleep(0.5)

    count = control.completed_count()
    ids = control.completed_ids()
    pending = control.pending_depth()
    expected = set(range(1, NUM_JOBS + 1))

    print("\n--- results ---")
    print(f"interruptions fired : {interruptions['count']}")
    print(f"workers spawned     : {next_id}")
    print(f"completed_count     : {count}")
    print(f"unique completed ids: {len(ids)}")
    print(f"pending left over   : {pending}")

    missing = expected - ids
    extra = ids - expected
    ok = (count == NUM_JOBS and ids == expected and pending == 0)

    if missing:
        print(f"LOST jobs          : {sorted(missing)}")
    if extra:
        print(f"UNEXPECTED ids     : {sorted(extra)}")
    if count != len(ids):
        print(f"DOUBLE-COUNTED     : count {count} != unique {len(ids)}")

    print("\n" + ("PASS: all 50 jobs completed exactly once, none lost."
                  if ok else "FAIL: exactly-once guarantee violated."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
