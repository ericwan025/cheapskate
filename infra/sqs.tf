# The job queue and its dead-letter queue.
#
# This is the AWS analog of the local Redis queue. SQS itself provides the
# reliability the Redis pattern hand-rolled: a received message stays invisible
# (not deleted) until the worker explicitly deletes it on completion. If a worker
# is spot-interrupted mid-job and exits without deleting, the visibility timeout
# lapses and the message reappears for another worker — so no job is lost.

# Dead-letter queue: messages that fail max_receive_count times land here instead
# of looping forever (a poison pill would otherwise block a worker indefinitely).
resource "aws_sqs_queue" "jobs_dlq" {
  name                      = "${var.project}-jobs-dlq"
  message_retention_seconds = var.dlq_retention_seconds
}

resource "aws_sqs_queue" "jobs" {
  name                       = "${var.project}-jobs"
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.message_retention_seconds

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

# Let the DLQ only accept redrives from our main queue (tidy least-privilege on
# the queue side, mirrors the IAM least-privilege we apply to the workers).
resource "aws_sqs_queue_redrive_allow_policy" "dlq_allow" {
  queue_url = aws_sqs_queue.jobs_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.jobs.arn]
  })
}
