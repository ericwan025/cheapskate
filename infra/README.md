# infra — Phase 3 AWS provisioning (Terraform)

Provisions the AWS side of cheapskate. Built incrementally, one commit per piece:

- [x] **SQS** — job queue + dead-letter queue (`sqs.tf`)
- [x] **IAM** — least-privilege worker role + instance profile (`iam.tf`)
- [x] **ECR + launch template** — worker image repo + boot config (`ecr.tf`, `launch_template.tf`)
- [ ] ASGs — spot + on-demand on the same launch template

## Credentials

Nothing sensitive is in the code. Terraform uses your existing AWS credentials
the same way the AWS CLI does — set `AWS_PROFILE` (or `aws_profile` in
`terraform.tfvars`) or export `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.

## Usage

```sh
cd infra
cp terraform.tfvars.example terraform.tfvars   # optional; all vars have defaults
terraform init
terraform plan
terraform apply
```

`terraform.tfvars` and all `*.tfstate` are git-ignored.
