# Values other pieces (workers, orchestrator, later Terraform files) consume.
output "jobs_queue_url" {
  description = "URL of the main job queue — workers poll this, producer sends to it."
  value       = aws_sqs_queue.jobs.url
}

output "jobs_queue_arn" {
  description = "ARN of the main job queue — referenced by the worker IAM policy."
  value       = aws_sqs_queue.jobs.arn
}

output "jobs_queue_name" {
  description = "Name of the main job queue (for CloudWatch dimensions / dashboards)."
  value       = aws_sqs_queue.jobs.name
}

output "jobs_dlq_url" {
  description = "URL of the dead-letter queue — inspect failed jobs here."
  value       = aws_sqs_queue.jobs_dlq.url
}

output "worker_role_arn" {
  description = "ARN of the least-privilege worker IAM role."
  value       = aws_iam_role.worker.arn
}

output "worker_instance_profile" {
  description = "Instance profile name to attach to the worker launch template."
  value       = aws_iam_instance_profile.worker.name
}
