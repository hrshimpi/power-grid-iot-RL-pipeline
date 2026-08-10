terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.4"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }

  # ── remote state (documented for future scope, not enabled) ──────────────
  # Local state (the default — a terraform.tfstate file on disk, gitignored)
  # is fine for one person working solo, which is the case today. For a team
  # sharing this deployment, uncomment and point this at an S3 bucket +
  # DynamoDB table you've created ahead of time — a backend can't provision
  # its own storage, so that bucket/table must already exist before
  # `terraform init` can use them.
  #
  # backend "s3" {
  #   bucket         = "your-terraform-state-bucket"  # must already exist
  #   key            = "grid-iot-rl/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "your-terraform-locks-table"    # for state locking, must already exist
  #   encrypt        = true
  # }
}
