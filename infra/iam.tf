# Least-privilege IAM for the worker EC2 instances.
#
# The workers need exactly two capabilities and nothing more:
#   1. Pull jobs from — and delete them on completion from — the ONE job queue.
#   2. Send jobs to the dead-letter queue (SQS does the redrive, but ReceiveMessage
#      on the DLQ lets us inspect/reprocess if we ever want to).
# Every action is scoped to our specific queue ARNs — no "Resource": "*".

# Trust policy: only the EC2 service may assume this role.
data "aws_iam_policy_document" "worker_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker" {
  name               = "${var.project}-worker"
  assume_role_policy = data.aws_iam_policy_document.worker_assume.json
}

# Permissions policy: queue operations, scoped to our queue ARNs only.
data "aws_iam_policy_document" "worker_queue" {
  statement {
    sid    = "ConsumeJobQueue"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [
      aws_sqs_queue.jobs.arn,
      aws_sqs_queue.jobs_dlq.arn,
    ]
  }
}

resource "aws_iam_role_policy" "worker_queue" {
  name   = "${var.project}-worker-queue"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker_queue.json
}

# Instance profile is what actually attaches the role to an EC2 instance /
# launch template.
resource "aws_iam_instance_profile" "worker" {
  name = "${var.project}-worker"
  role = aws_iam_role.worker.name
}
