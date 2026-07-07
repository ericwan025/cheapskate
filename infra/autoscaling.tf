# Two Auto Scaling Groups on the SAME launch template, differing only in HOW they
# buy capacity:
#   - spot ASG      -> 100% EC2 spot (cheap, can be interrupted)
#   - on-demand ASG -> 100% on-demand (reliable baseline)
# The orchestrator (Phase 3 code) sets each group's desired_capacity via the Auto
# Scaling API to rebalance cost vs. reliability against queue depth + the recent
# interruption rate.
#
# Both default to desired=0, so applying this stack costs $0 in EC2 until you
# deliberately scale up to test.

# --- Spot fleet -------------------------------------------------------------
resource "aws_autoscaling_group" "spot" {
  name                = "${var.project}-spot"
  vpc_zone_identifier = data.aws_subnets.default.ids

  min_size         = var.spot_min_size
  max_size         = var.spot_max_size
  desired_capacity = var.spot_desired_capacity

  # Give a spot instance time to drain (worker requeues its job on the 2-minute
  # warning) before ASG health checks fret about it.
  health_check_grace_period = 90
  capacity_rebalance        = true # proactively replace at-risk spot instances

  mixed_instances_policy {
    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.worker.id
        version            = "$Latest"
      }
      # Diversify across a few instance types so spot capacity is easier to get.
      dynamic "override" {
        for_each = var.spot_instance_types
        content {
          instance_type = override.value
        }
      }
    }

    instances_distribution {
      on_demand_base_capacity                  = 0
      on_demand_percentage_above_base_capacity = 0 # 100% spot
      spot_allocation_strategy                 = "price-capacity-optimized"
    }
  }

  tag {
    key                 = "Name"
    value               = "${var.project}-spot"
    propagate_at_launch = true
  }
  tag {
    key                 = "cheapskate:purchase"
    value               = "spot"
    propagate_at_launch = true
  }

  # The orchestrator owns desired_capacity at runtime; don't let Terraform fight it.
  lifecycle {
    ignore_changes = [desired_capacity]
  }
}

# --- On-demand baseline -----------------------------------------------------
resource "aws_autoscaling_group" "on_demand" {
  name                = "${var.project}-on-demand"
  vpc_zone_identifier = data.aws_subnets.default.ids

  min_size         = var.on_demand_min_size
  max_size         = var.on_demand_max_size
  desired_capacity = var.on_demand_desired_capacity

  health_check_grace_period = 90

  launch_template {
    id      = aws_launch_template.worker.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${var.project}-on-demand"
    propagate_at_launch = true
  }
  tag {
    key                 = "cheapskate:purchase"
    value               = "on-demand"
    propagate_at_launch = true
  }

  lifecycle {
    ignore_changes = [desired_capacity]
  }
}

output "spot_asg_name" {
  description = "Spot ASG name — orchestrator adjusts its desired capacity."
  value       = aws_autoscaling_group.spot.name
}

output "on_demand_asg_name" {
  description = "On-demand ASG name — orchestrator adjusts its desired capacity."
  value       = aws_autoscaling_group.on_demand.name
}
