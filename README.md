# cheapskate
cheapskate is a cost-aware autoscaler for AWS EC2 spot fleets. It dynamically balances spot and on-demand instances based on queue depth and interruption rate, gracefully handling AWS's 2-minute spot interruption warning so in-flight jobs are requeued instead of lost. Built with Docker, Terraform, and SQS.
