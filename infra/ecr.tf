# Private registry that holds the worker image the EC2 instances pull on boot.
resource "aws_ecr_repository" "worker" {
  name                 = "${var.project}-worker"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Keep the repo tidy: expire all but the most recent images so old builds don't
# accrue storage cost.
resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the last ${var.ecr_keep_last_images} images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.ecr_keep_last_images
      }
      action = { type = "expire" }
    }]
  })
}

# Least-privilege ECR pull for the workers, scoped to THIS repo. GetAuthorizationToken
# has no resource scope in AWS (must be "*"), but the actual image-pull actions are
# locked to our repository ARN.
data "aws_iam_policy_document" "worker_ecr" {
  statement {
    sid       = "EcrAuthToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullWorkerImage"
    effect = "Allow"
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchCheckLayerAvailability",
    ]
    resources = [aws_ecr_repository.worker.arn]
  }
}

resource "aws_iam_role_policy" "worker_ecr" {
  name   = "${var.project}-worker-ecr"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker_ecr.json
}

output "worker_ecr_repo_url" {
  description = "Push the worker image here (docker build/tag/push target)."
  value       = aws_ecr_repository.worker.repository_url
}
