variable "project" {
  description = "Project name prefix for all resources"
  type        = string
  default     = "grid-iot-rl"
}

variable "env" {
  description = "Environment (dev / staging / prod)"
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "alert_email" {
  description = "Email for anomaly and security alerts"
  type        = string
}

variable "hmac_secret" {
  description = "Shared HMAC secret between edge devices and Lambda"
  type        = string
  sensitive   = true
}

variable "devices" {
  description = "List of IoT device names to provision"
  type        = list(string)
  default     = ["edge-device-001", "edge-device-002", "edge-device-003"]
}

variable "sagemaker_endpoint" {
  description = "SageMaker RL endpoint name (deploy separately via notebook)"
  type        = string
  default     = "grid-voltage-rl-v1"
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 30
}

variable "lambda_memory" {
  description = "Lambda memory in MB"
  type        = number
  default     = 256
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "private_subnet_cidrs" {
  description = "Private subnet CIDRs (one per AZ)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "public_subnet_cidrs" {
  description = "Public subnet CIDRs (for NAT gateway)"
  type        = list(string)
  default     = ["10.0.101.0/24", "10.0.102.0/24"]
}

variable "s3_force_destroy" {
  description = "Allow terraform destroy to delete non-empty S3 bucket"
  type        = bool
  default     = true
}
