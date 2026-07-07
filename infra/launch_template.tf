# Launch template shared by BOTH ASGs (spot + on-demand). The purchase option is
# NOT set here — it's decided per-ASG in autoscaling.tf — so one template defines
# the machine and the two groups differ only in how they buy capacity.

# Latest Amazon Linux 2023 AMI, resolved at plan time (unless overridden).
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# Default VPC networking — enough for a demo fleet that only makes outbound calls.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# Workers only talk OUT (SQS, ECR). No inbound is allowed at all.
resource "aws_security_group" "worker" {
  name        = "${var.project}-worker"
  description = "cheapskate workers: egress-only"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "Allow all outbound (SQS, ECR, metadata)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

locals {
  ami_id        = var.ami_id != "" ? var.ami_id : data.aws_ssm_parameter.al2023.value
  ecr_registry  = split("/", aws_ecr_repository.worker.repository_url)[0]
  worker_image  = "${aws_ecr_repository.worker.repository_url}:${var.worker_image_tag}"

  # Boot script: install Docker, authenticate to ECR, pull the worker image, and
  # run it pointed at SQS. The worker itself polls the spot-interruption metadata
  # endpoint and drains gracefully (implemented in the worker code).
  user_data = base64encode(<<-EOT
    #!/bin/bash
    set -euxo pipefail
    dnf update -y
    dnf install -y docker
    systemctl enable --now docker

    aws ecr get-login-password --region ${var.aws_region} \
      | docker login --username AWS --password-stdin ${local.ecr_registry}

    docker pull ${local.worker_image}

    docker run -d --name cheapskate-worker --restart unless-stopped \
      -e QUEUE_BACKEND=sqs \
      -e SQS_QUEUE_URL=${aws_sqs_queue.jobs.url} \
      -e AWS_REGION=${var.aws_region} \
      -e AWS_DEFAULT_REGION=${var.aws_region} \
      -e JOB_MIN_SECONDS=${var.instance_job_min_seconds} \
      -e JOB_MAX_SECONDS=${var.instance_job_max_seconds} \
      -e SPOT_POLL_SECONDS=${var.poll_interval_seconds} \
      ${local.worker_image}
  EOT
  )
}

resource "aws_launch_template" "worker" {
  name_prefix   = "${var.project}-worker-"
  image_id      = local.ami_id
  instance_type = var.instance_type
  user_data     = local.user_data

  iam_instance_profile {
    name = aws_iam_instance_profile.worker.name
  }

  vpc_security_group_ids = [aws_security_group.worker.id]

  # Require IMDSv2 (token-authenticated metadata) — the secure default, and the
  # worker uses it to read the spot-interruption notice.
  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.project}-worker" }
  }

  update_default_version = true
}

output "worker_launch_template_id" {
  value       = aws_launch_template.worker.id
  description = "Launch template both ASGs build instances from."
}
