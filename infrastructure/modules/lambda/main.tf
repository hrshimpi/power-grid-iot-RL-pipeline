# package lambda source code
data "archive_file" "lambda" {
  type        = "zip"
  source_file = "${path.root}/lambda/src/lambda_function.py"
  output_path = "${path.root}/lambda/build/lambda.zip"
}

# CloudWatch log group
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.prefix}-inference"
  retention_in_days = 14
}

# Lambda function — runs inside VPC private subnet
resource "aws_lambda_function" "inference" {
  function_name    = "${var.prefix}-inference"
  role             = var.role_arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.11"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = var.timeout
  memory_size      = var.memory

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.lambda_sg_id]
  }

  environment {
    variables = {
      S3_BUCKET          = var.s3_bucket_name
      SNS_TOPIC_ARN      = var.sns_topic_arn
      HMAC_SECRET_ARN    = var.hmac_secret_arn
      SAGEMAKER_ENDPOINT = var.sagemaker_endpoint
      AWS_ACCOUNT_ID     = var.account_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}
