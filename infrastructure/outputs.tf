output "iot_endpoint" {
  description = "IoT Core MQTT endpoint — use as IOT_ENDPOINT in edge simulator"
  value       = module.iot.endpoint
}

output "s3_bucket" {
  description = "Data lake S3 bucket name"
  value       = module.storage.bucket_name
}

output "lambda_function" {
  description = "Lambda function name"
  value       = module.lambda.function_name
}

output "sns_topic" {
  description = "SNS alert topic ARN"
  value       = module.alerting.topic_arn
}

output "hmac_secret_arn" {
  description = "Secrets Manager ARN for HMAC secret"
  value       = module.secrets.hmac_secret_arn
}

output "sagemaker_execution_role_arn" {
  description = "IAM role ARN to pass as ExecutionRoleArn when deploying the SageMaker model from the notebook"
  value       = module.iam.sagemaker_role_arn
}

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "device_certs_dir" {
  description = "Local folder where device certs were saved"
  value       = "${path.root}/../certs/"
}

output "next_steps" {
  description = "What to do after terraform apply"
  value       = <<-EOT
    1. Confirm SNS subscription email in your inbox
    2. Copy certs to edge simulator:
         cp -r ../certs/* ../edge_simulator/local/certs/
         cp -r ../certs/* ../edge_simulator/colab/certs/
    3. Open rl_model/grid_voltage_notebook.ipynb and run all cells
    4. Run edge simulator:
         cd ../edge_simulator/local
         python publish_to_iot.py --count 10 --mode happy
  EOT
}
