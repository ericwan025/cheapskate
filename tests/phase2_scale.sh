#!/usr/bin/env bash
# Phase 2 end-to-end test: the orchestrator ACTUATES the fleet.
#
# Unlike phase1, we do NOT start any workers ourselves. We bring up only redis +
# orchestrator, dump a burst of jobs in, and prove the orchestrator:
#   1. scales the worker fleet UP on its own (launches containers via Docker),
#   2. drains every job exactly once (none lost, no dupes),
#   3. scales the fleet back DOWN to MIN_WORKERS once the queue empties.
#
# Usage:  ./tests/phase2_scale.sh [NUM_JOBS]
set -euo pipefail
cd "$(dirname "$0")/.."

JOBS="${1:-40}"
MIN_WORKERS="${MIN_WORKERS:-1}"

# Speed things up: jobs take 1-4s instead of the production-y 5-30s.
export JOB_MIN_SECONDS="${JOB_MIN_SECONDS:-1.0}"
export JOB_MAX_SECONDS="${JOB_MAX_SECONDS:-4.0}"

LABEL="cheapskate.managed-by=orchestrator"

worker_count() { docker ps -q --filter "label=$LABEL" | wc -l | tr -d '[:space:]'; }

echo "==> removing any leftover stack"
docker compose down -v --remove-orphans >/dev/null 2>&1 || true
# Also sweep any managed workers the orchestrator launched (they live outside
# compose, so `compose down` won't touch them).
docker ps -aq --filter "label=$LABEL" | xargs -r docker rm -f >/dev/null 2>&1 || true

echo "==> starting redis + orchestrator (NO workers — orchestrator owns the fleet)"
docker compose up -d --build redis orchestrator

LOG_DIR="${LOG_DIR:-/tmp/cheapskate-phase2}"
mkdir -p "$LOG_DIR"

cleanup() {
  echo "==> capturing logs to $LOG_DIR"
  docker compose logs --no-color > "$LOG_DIR/stack.log" 2>&1 || true
  docker ps -a --filter "label=$LABEL" > "$LOG_DIR/workers.txt" 2>&1 || true
  echo "==> tearing down"
  docker compose down -v >/dev/null 2>&1 || true
  docker ps -aq --filter "label=$LABEL" | xargs -r docker rm -f >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> waiting for redis"
for _ in $(seq 1 30); do
  docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG && break
  sleep 1
done
docker compose exec -T redis redis-cli flushall >/dev/null

echo "==> pushing $JOBS jobs"
docker compose run --rm --no-deps -e JOB_COUNT="$JOBS" producer

echo "==> watching the orchestrator scale the fleet UP"
peak=0
for _ in $(seq 1 30); do
  n="$(worker_count)"
  [ "$n" -gt "$peak" ] && peak="$n"
  echo "    running workers=$n (peak=$peak)"
  [ "$peak" -ge 2 ] && break
  sleep 1
done

echo "==> waiting for all $JOBS jobs to complete"
INFLIGHT_LUA="local s=0 for _,k in ipairs(redis.call('keys','jobs:processing:*')) do s=s+redis.call('llen',k) end return s"
for _ in $(seq 1 120); do
  completed="$(docker compose exec -T redis redis-cli get jobs:completed_count 2>/dev/null | tr -d '[:space:]')"
  completed="${completed:-0}"
  pending="$(docker compose exec -T redis redis-cli llen jobs:pending 2>/dev/null | tr -d '[:space:]')"
  inflight="$(docker compose exec -T redis redis-cli eval "$INFLIGHT_LUA" 0 2>/dev/null | tr -d '[:space:]')"
  echo "    completed=$completed pending=${pending:-?} inflight=${inflight:-?} workers=$(worker_count)"
  if [ "$completed" -ge "$JOBS" ] && [ "${pending:-1}" = "0" ]; then break; fi
  sleep 2
done

echo "==> waiting for the orchestrator to scale the fleet back DOWN to $MIN_WORKERS"
settled=0
for _ in $(seq 1 30); do
  n="$(worker_count)"
  echo "    running workers=$n"
  if [ "$n" -le "$MIN_WORKERS" ]; then settled=1; break; fi
  sleep 2
done

count="$(docker compose exec -T redis redis-cli get jobs:completed_count | tr -d '[:space:]')"
unique="$(docker compose exec -T redis redis-cli scard jobs:completed | tr -d '[:space:]')"

echo
echo "--- results ---"
echo "peak workers launched : $peak   (expected >= 2)"
echo "completed_count       : $count"
echo "unique completed ids  : $unique"
echo "scaled back down      : $([ "$settled" = 1 ] && echo yes || echo no)"

if [ "$count" = "$JOBS" ] && [ "$unique" = "$JOBS" ] && [ "$peak" -ge 2 ] && [ "$settled" = 1 ]; then
  echo "PASS: orchestrator scaled up, drained all $JOBS jobs exactly once, and scaled back down."
  exit 0
else
  echo "FAIL."
  exit 1
fi
