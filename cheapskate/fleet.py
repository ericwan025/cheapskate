"""Fleet snapshot — how many workers are running, and what they cost (Phase 4).

Backend-aware, mirroring the actuator split:
  - redis (local): count the docker containers the orchestrator manages. There's
    no spot/on-demand distinction locally, so they land in a single "local" bucket.
  - sqs (AWS): describe the two ASGs and count in-service instances per purchase
    type -> {"spot": s, "on_demand": o}.

Everything is best-effort: if docker or AWS is unreachable, we return an empty
fleet rather than raising, so the dashboard still renders queue stats.
"""
from __future__ import annotations

import logging

from . import config

log = logging.getLogger("fleet")

# Per-bucket approximate hourly cost, for the live burn-rate estimate.
_HOURLY_COST = {
    "spot": config.SPOT_HOURLY_COST,
    "on_demand": config.ON_DEMAND_HOURLY_COST,
    # A local docker worker has no real price; bill it at the on-demand rate so
    # the estimate is meaningful during local testing.
    "local": config.ON_DEMAND_HOURLY_COST,
}


def fleet_counts() -> dict[str, int]:
    """Worker counts per purchase bucket. Empty dict if the fleet is unreachable."""
    try:
        if config.QUEUE_BACKEND == "sqs":
            return _asg_counts()
        return _docker_counts()
    except Exception as exc:  # noqa: BLE001 - dashboard must not crash on this
        log.warning("fleet snapshot failed: %s", exc)
        return {}


def cost_per_hour(counts: dict[str, int]) -> float:
    """Instantaneous burn rate ($/hr) implied by the current fleet."""
    return round(
        sum(_HOURLY_COST.get(bucket, 0.0) * n for bucket, n in counts.items()), 4
    )


def _docker_counts() -> dict[str, int]:
    import docker

    client = docker.from_env()
    workers = client.containers.list(
        filters={
            "label": f"{config.MANAGED_LABEL}={config.MANAGED_VALUE}",
            "status": "running",
        }
    )
    return {"local": len(workers)} if workers else {}


def _asg_counts() -> dict[str, int]:
    import boto3

    client = boto3.client("autoscaling", region_name=config.AWS_REGION or None)
    names = [n for n in (config.SPOT_ASG_NAME, config.ON_DEMAND_ASG_NAME) if n]
    if not names:
        return {}
    groups = {
        g["AutoScalingGroupName"]: g
        for g in client.describe_auto_scaling_groups(AutoScalingGroupNames=names)[
            "AutoScalingGroups"
        ]
    }

    def in_service(name: str) -> int:
        g = groups.get(name)
        if not g:
            return 0
        return sum(
            1 for i in g.get("Instances", []) if i.get("LifecycleState") == "InService"
        )

    return {
        "spot": in_service(config.SPOT_ASG_NAME),
        "on_demand": in_service(config.ON_DEMAND_ASG_NAME),
    }
