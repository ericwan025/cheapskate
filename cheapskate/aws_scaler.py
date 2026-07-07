"""AWS Auto Scaling actuator (Phase 3).

The real-fleet analog of the local DockerScaler. The orchestrator decides *how
many* workers it wants from queue depth; this module makes it so on AWS — but it
also decides *what kind*: it splits that total across two Auto Scaling Groups,
one 100% spot (cheap, interruptible) and one on-demand (reliable), by calling
`SetDesiredCapacity` on each.

The split is cost-aware but reliability-aware:
  - A baseline fraction (ON_DEMAND_BASE_FRACTION) always rides on on-demand, so a
    spot mass-reclaim can never take the whole fleet to zero.
  - On top of that baseline, an *interruption-pressure* signal shifts more of the
    fleet onto on-demand when spot is actually being taken from us. The signal is
    an EWMA of the spot ASG's capacity shortfall — desired spot capacity minus the
    instances actually in service. When spot is healthy the shortfall is ~0 and we
    stay maximally cheap; when AWS is reclaiming spot faster than it can be
    replaced, the shortfall (and thus the on-demand share) rises.

Same contract the orchestrator already uses against DockerScaler: `count()` and
`scale_to(total_desired)`. `cleanup()` scales both groups to zero on shutdown so
an idle stack costs nothing.

Construction raises if boto3 / the ASGs aren't reachable; the orchestrator
catches that and falls back to log-only mode.
"""
from __future__ import annotations

import logging
import math

import boto3

from . import config

log = logging.getLogger("aws-scaler")


class AsgScaler:
    def __init__(self) -> None:
        self.spot_asg = config.SPOT_ASG_NAME
        self.on_demand_asg = config.ON_DEMAND_ASG_NAME
        if not self.spot_asg or not self.on_demand_asg:
            raise RuntimeError(
                "SPOT_ASG_NAME and ON_DEMAND_ASG_NAME are required in SQS/AWS mode"
            )
        region = config.AWS_REGION or None
        self.client = boto3.client("autoscaling", region_name=region)
        # Fail fast if either ASG doesn't exist / we can't reach the API.
        self._describe(self.spot_asg)
        self._describe(self.on_demand_asg)
        # EWMA of the spot capacity shortfall, in [0, 1]. Starts optimistic (0).
        self.interruption_pressure = 0.0
        log.info(
            "asg scaler ready — spot=%s on_demand=%s base_on_demand=%.0f%%",
            self.spot_asg,
            self.on_demand_asg,
            config.ON_DEMAND_BASE_FRACTION * 100,
        )

    # --- introspection ---------------------------------------------------
    def _describe(self, name: str) -> dict:
        groups = self.client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[name]
        )["AutoScalingGroups"]
        if not groups:
            raise RuntimeError(f"auto scaling group not found: {name}")
        return groups[0]

    def _in_service(self, group: dict) -> int:
        """Instances actually running and healthy (not pending/terminating)."""
        return sum(
            1
            for i in group.get("Instances", [])
            if i.get("LifecycleState") == "InService"
        )

    def count(self) -> int:
        """Current total desired capacity across both groups (for logging)."""
        return (
            self._describe(self.spot_asg)["DesiredCapacity"]
            + self._describe(self.on_demand_asg)["DesiredCapacity"]
        )

    # --- interruption signal --------------------------------------------
    def _update_pressure(self, spot_group: dict) -> None:
        """Fold the spot ASG's current capacity shortfall into the EWMA.

        shortfall = (desired - in_service) / desired, clamped to [0, 1]. It's the
        fraction of the spot capacity we asked for that AWS isn't currently giving
        us — a live proxy for the recent interruption rate.
        """
        desired = spot_group["DesiredCapacity"]
        if desired <= 0:
            sample = 0.0
        else:
            shortfall = (desired - self._in_service(spot_group)) / desired
            sample = max(0.0, min(1.0, shortfall))
        a = config.INTERRUPTION_EWMA_ALPHA
        self.interruption_pressure = a * sample + (1 - a) * self.interruption_pressure

    # --- actuation -------------------------------------------------------
    def _split(self, total: int) -> tuple[int, int]:
        """Split `total` desired workers into (spot, on_demand).

        on_demand share = baseline + interruption_pressure * (1 - baseline).
        Everything left goes to spot. Each side is clamped to its ASG max.
        """
        if total <= 0:
            return 0, 0
        base = config.ON_DEMAND_BASE_FRACTION
        on_demand_frac = base + self.interruption_pressure * (1 - base)
        on_demand = math.ceil(total * on_demand_frac)
        on_demand = min(on_demand, config.ON_DEMAND_MAX_CAPACITY, total)
        spot = min(total - on_demand, config.SPOT_MAX_CAPACITY)
        return spot, on_demand

    def scale_to(self, total_desired: int) -> None:
        spot_group = self._describe(self.spot_asg)
        self._update_pressure(spot_group)
        spot, on_demand = self._split(total_desired)

        cur_spot = spot_group["DesiredCapacity"]
        cur_on_demand = self._describe(self.on_demand_asg)["DesiredCapacity"]

        log.info(
            "want %d -> spot=%d on_demand=%d (pressure=%.2f)",
            total_desired,
            spot,
            on_demand,
            self.interruption_pressure,
        )
        if spot != cur_spot:
            self._set(self.spot_asg, spot)
        if on_demand != cur_on_demand:
            self._set(self.on_demand_asg, on_demand)

    def _set(self, name: str, desired: int) -> None:
        try:
            self.client.set_desired_capacity(
                AutoScalingGroupName=name,
                DesiredCapacity=desired,
                HonorCooldown=False,
            )
        except Exception as exc:  # noqa: BLE001 - best effort; retried next cycle
            log.warning("set_desired_capacity(%s, %d) failed: %s", name, desired, exc)

    def cleanup(self) -> None:
        """Scale both groups to zero so an idle/stopped stack costs nothing."""
        log.info("cleanup — scaling both ASGs to 0")
        self._set(self.spot_asg, 0)
        self._set(self.on_demand_asg, 0)
