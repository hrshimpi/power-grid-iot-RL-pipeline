output "endpoint" { value = data.aws_iot_endpoint.ats.endpoint_address }
output "policy_name" { value = aws_iot_policy.device.name }
