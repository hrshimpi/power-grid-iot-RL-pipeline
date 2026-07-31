variable "prefix" { type = string }
variable "account_id" { type = string }
variable "role_arn" { type = string }
variable "timeout" {
  type    = number
  default = 30
}

variable "memory" {
  type    = number
  default = 256
}
variable "s3_bucket_name" { type = string }
variable "sns_topic_arn" { type = string }
variable "hmac_secret_arn" { type = string }
variable "sagemaker_endpoint" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "lambda_sg_id" { type = string }
