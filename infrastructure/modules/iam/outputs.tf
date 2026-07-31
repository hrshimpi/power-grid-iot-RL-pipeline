output "lambda_role_arn" { value = aws_iam_role.lambda.arn }
output "iot_rule_error_action_role_arn" { value = aws_iam_role.iot_rule_error_action.arn }
output "sagemaker_role_arn" { value = aws_iam_role.sagemaker.arn }
