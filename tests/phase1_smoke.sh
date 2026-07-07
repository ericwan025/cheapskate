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

echo "==> building + starting fleet ($WORKERS workers) + redis + orchestrator"
docker compose up -d --build --scale worker="$WORKERS" redis worker orchestrator

cleanup() {
  echo "==> tearing down"
  docker compose down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> waiting for redis to be healthy"
for _ in $(seq 1 30); do
  if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then break; fi
  sleep 1
done

echo "==> pushing $JOBS jobs"
docker compose run --rm producer "$JOBS"

echo "==> firing $INTERRUPTS random interruptions while the fleet drains"
for i in $(seq 1 "$INTERRUPTS"); do
  docker compose run --rm interrupter 1 || true
  sleep 1
done

echo "==> waiting for all $JOBS jobs to complete"
completed=0
for _ in $(seq 1 120); do
  completed="$(docker compose exec -T redis redis-cli get jobs:completed_count 2>/dev/null | tr -d '[:space:]')"
  completed="${completed:-0}"
  pending="$(docker compose exec -T redis redis-cli llen jobs:pending 2>/dev/null | tr -d '[:space:]')"
  echo "    completed=$completed pending=${pending:-?}"
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
