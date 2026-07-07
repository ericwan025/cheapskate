# AWS provider. Credentials are NEVER hardcoded here — they come from the
# environment (AWS_PROFILE / AWS_ACCESS_KEY_ID etc.) or the shared config file,
# exactly as the AWS CLI resolves them. Region is a variable so nothing is
# account- or region-specific in the code.
provider "aws" {
  region = var.aws_region

  # Optionally assume a role / use a named profile without editing code.
  profile = var.aws_profile != "" ? var.aws_profile : null

  # Tag everything this stack creates, so costs and ownership are attributable.
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}
