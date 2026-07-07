#!/usr/bin/env bash
# Phase 1 end-to-end smoke test against the real docker-compose stack.
#
# Brings up Redis + a worker fleet + orchestrator, pushes 50 jobs, then hammers
# the fleet with random spot-interruptions while it drains. Finally asserts the
# exactly-once guarantee straight from Redis:
#     completed_count == 50  AND  |completed set| == 50
#
# Usage:  ./tests/phase1_smoke.sh [NUM_JOBS] [NUM_WORKERS] [NUM_INTERRUPTS]
set -euo pipefail
cd "$(dirname "$0")/.."

JOBS="${1:-50}"
WORKERS="${2:-4}"
INTERRUPTS="${3:-10}"

# Speed the test up: jobs take 1-4s instead of the production-y 5-30s.
# Override by exporting these before running.
export JOB_MIN_SECONDS="${JOB_MIN_SECONDS:-1.0}"
export JOB_MAX_SECONDS="${JOB_MAX_SECONDS:-4.0}"

# Start from a clean slate: a previous run killed mid-way (terminal closed,
# Ctrl-Z, crash) leaves containers + Redis data behind, which would poison the
# exactly-once accounting below.
echo "==> removing any leftover stack from a previous run"
docker compose down -v --remove-orphans >/dev/null 2>&1 || true

echo "==> building + starting fleet ($WORKERS workers) + redis + orchestrator"
docker compose up -d --build --scale worker="$WORKERS" redis worker orchestrator

LOG_DIR="${LOG_DIR:-/tmp/cheapskate-smoke}"
mkdir -p "$LOG_DIR"

capture_evidence() {
  # Preserve everything needed to diagnose a failure before teardown wipes it.
  echo "==> capturing logs + queue state to $LOG_DIR"
  docker compose logs --no-color > "$LOG_DIR/containers.log" 2>&1 || true
  docker compose ps -a > "$LOG_DIR/ps.txt" 2>&1 || true
  {
    echo "pending:"
    docker compose exec -T redis redis-cli lrange jobs:pending 0 -1
    echo "completed_count: $(docker compose exec -T redis redis-cli get jobs:completed_count)"
    echo "completed ids: $(docker compose exec -T redis redis-cli smembers jobs:completed | sort -n | tr '\n' ' ')"
    echo "processing lists:"
    docker compose exec -T redis redis-cli --scan --pattern 'jobs:processing:*' | while read -r key; do
      echo "  $key -> $(docker compose exec -T redis redis-cli lrange "$key" 0 -1 | tr '\n' ' ')"
    done
  } > "$LOG_DIR/redis-state.txt" 2>&1 || true
}

cleanup() {
  capture_evidence
  echo "==> tearing down"
  docker compose down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> waiting for redis to be healthy"
for _ in $(seq 1 30); do
  if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then break; fi
  sleep 1
done

# Belt-and-suspenders: guarantee empty queue state even if a stray redis
# volume survived.
docker compose exec -T redis redis-cli flushall >/dev/null

# One-shot tools are driven by env vars, not positional args: `docker compose
# run SERVICE <token>` REPLACES the container command with <token>, so a
# trailing "50" would be exec'd as a program. JOB_COUNT / INTERRUPT_COUNT keep
# the service's real command intact.
# --no-deps is load-bearing: without it `docker compose run` reconciles the
# tool's dependencies to their configured scale (1), stopping and REMOVING the
# extra worker replicas — which strands their in-flight jobs.
echo "==> pushing $JOBS jobs"
docker compose run --rm --no-deps -e JOB_COUNT="$JOBS" producer

echo "==> firing $INTERRUPTS random interruptions while the fleet drains"
for i in $(seq 1 "$INTERRUPTS"); do
  docker compose run --rm --no-deps -e INTERRUPT_COUNT=1 interrupter || true
  sleep 1
done

echo "==> waiting for all $JOBS jobs to complete"
INFLIGHT_LUA="local s=0 for _,k in ipairs(redis.call('keys','jobs:processing:*')) do s=s+redis.call('llen',k) end return s"
completed=0
for _ in $(seq 1 120); do
  completed="$(docker compose exec -T redis redis-cli get jobs:completed_count 2>/dev/null | tr -d '[:space:]')"
  completed="${completed:-0}"
  pending="$(docker compose exec -T redis redis-cli llen jobs:pending 2>/dev/null | tr -d '[:space:]')"
  inflight="$(docker compose exec -T redis redis-cli eval "$INFLIGHT_LUA" 0 2>/dev/null | tr -d '[:space:]')"
  echo "    completed=$completed pending=${pending:-?} inflight=${inflight:-?}"
  if [ "$completed" -ge "$JOBS" ] && [ "${pending:-1}" = "0" ]; then break; fi
  sleep 2
done

count="$(docker compose exec -T redis redis-cli get jobs:completed_count | tr -d '[:space:]')"
unique="$(docker compose exec -T redis redis-cli scard jobs:completed | tr -d '[:space:]')"

echo
echo "--- results ---"
echo "completed_count     : $count"
echo "unique completed ids: $unique"

if [ "$count" = "$JOBS" ] && [ "$unique" = "$JOBS" ]; then
  echo "PASS: all $JOBS jobs completed exactly once, none lost."
  exit 0
else
  echo "FAIL: expected $JOBS/$JOBS, got count=$count unique=$unique."
  exit 1
fi
