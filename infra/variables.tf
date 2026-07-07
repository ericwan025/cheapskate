# All account- and environment-specific values are variables — nothing sensitive
# or account-specific is hardcoded. Override via terraform.tfvars (git-ignored),
# -var flags, or TF_VAR_* env vars.

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Named AWS CLI profile to use. Empty string => default credential resolution (env vars / default profile)."
  type        = string
  default     = ""
}

variable "project" {
  description = "Name prefix applied to every resource, so runs don't collide."
  type        = string
  default     = "cheapskate"
}

# --- SQS job queue ----------------------------------------------------------

variable "visibility_timeout_seconds" {
  description = <<-EOT
    How long a reserved message is hidden from other workers. Must exceed the
    longest job plus the spot-interruption drain window, so a slow-but-alive
    worker doesn't have its message handed to someone else. Default 120s covers
    a 30s job + a 2-minute-warning drain.
  EOT
  type        = number
  default     = 120
}

variable "message_retention_seconds" {
  description = "How long an unconsumed job stays in the queue before SQS drops it. Default 4 days."
  type        = number
  default     = 345600
}

variable "max_receive_count" {
  description = "Times a message can be received before it's routed to the dead-letter queue (poison-pill protection)."
  type        = number
  default     = 5
}

variable "dlq_retention_seconds" {
  description = "How long failed jobs are kept in the dead-letter queue for inspection. Default 14 days."
  type        = number
  default     = 1209600
}

# --- ECR + launch template --------------------------------------------------

variable "ecr_keep_last_images" {
  description = "Number of recent worker images to retain in ECR; older ones expire."
  type        = number
  default     = 5
}

variable "worker_image_tag" {
  description = "Tag of the worker image (in ECR) the instances should run."
  type        = string
  default     = "latest"
}

variable "instance_type" {
  description = "EC2 instance type for workers. A small burstable type is plenty for the demo."
  type        = string
  default     = "t3.small"
}

variable "ami_id" {
  description = "Override the worker AMI. Empty => latest Amazon Linux 2023 x86_64 (resolved via SSM)."
  type        = string
  default     = ""
}

variable "poll_interval_seconds" {
  description = "How often the worker checks the EC2 spot-interruption metadata endpoint."
  type        = number
  default     = 5
}

variable "instance_job_min_seconds" {
  description = "Min simulated job duration on the real fleet."
  type        = number
  default     = 5
}

variable "instance_job_max_seconds" {
  description = "Max simulated job duration on the real fleet."
  type        = number
  default     = 30
}
