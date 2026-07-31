# one Thing per device
resource "aws_iot_thing" "devices" {
  for_each = toset(var.devices)
  name     = each.value
}

# one private key per device
resource "tls_private_key" "devices" {
  for_each  = toset(var.devices)
  algorithm = "RSA"
  rsa_bits  = 2048
}

# one CSR per device
resource "tls_cert_request" "devices" {
  for_each        = toset(var.devices)
  private_key_pem = tls_private_key.devices[each.value].private_key_pem
  subject {
    common_name  = each.value
    organization = var.prefix
  }
}

# one certificate per device
resource "aws_iot_certificate" "devices" {
  for_each = toset(var.devices)
  csr      = tls_cert_request.devices[each.value].cert_request_pem
  active   = true
}

# shared policy using ${iot:ClientId} wildcard — works for all devices
resource "aws_iot_policy" "device" {
  name = "${var.prefix}-device-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "iot:Connect"
        Resource = "arn:aws:iot:${var.region}:${var.account_id}:client/$${iot:ClientId}"
      },
      {
        Effect = "Allow"
        Action = "iot:Publish"
        Resource = [
          "arn:aws:iot:${var.region}:${var.account_id}:topic/grid/$${iot:ClientId}/telemetry",
          "arn:aws:iot:${var.region}:${var.account_id}:topic/$aws/things/$${iot:ClientId}/shadow/update",
          "arn:aws:iot:${var.region}:${var.account_id}:topic/$aws/things/$${iot:ClientId}/shadow/get"
        ]
      },
      {
        Effect = "Allow"
        Action = "iot:Subscribe"
        Resource = [
          "arn:aws:iot:${var.region}:${var.account_id}:topicfilter/grid/$${iot:ClientId}/telemetry",
          "arn:aws:iot:${var.region}:${var.account_id}:topicfilter/$aws/things/$${iot:ClientId}/shadow/update/accepted",
          "arn:aws:iot:${var.region}:${var.account_id}:topicfilter/$aws/things/$${iot:ClientId}/shadow/update/rejected",
          "arn:aws:iot:${var.region}:${var.account_id}:topicfilter/$aws/things/$${iot:ClientId}/shadow/update/delta",
          "arn:aws:iot:${var.region}:${var.account_id}:topicfilter/$aws/things/$${iot:ClientId}/shadow/get/accepted",
          "arn:aws:iot:${var.region}:${var.account_id}:topicfilter/$aws/things/$${iot:ClientId}/shadow/get/rejected"
        ]
      },
      {
        Effect = "Allow"
        Action = "iot:Receive"
        Resource = [
          "arn:aws:iot:${var.region}:${var.account_id}:topic/grid/$${iot:ClientId}/telemetry",
          "arn:aws:iot:${var.region}:${var.account_id}:topic/$aws/things/$${iot:ClientId}/shadow/update/accepted",
          "arn:aws:iot:${var.region}:${var.account_id}:topic/$aws/things/$${iot:ClientId}/shadow/update/rejected",
          "arn:aws:iot:${var.region}:${var.account_id}:topic/$aws/things/$${iot:ClientId}/shadow/update/delta",
          "arn:aws:iot:${var.region}:${var.account_id}:topic/$aws/things/$${iot:ClientId}/shadow/get/accepted",
          "arn:aws:iot:${var.region}:${var.account_id}:topic/$aws/things/$${iot:ClientId}/shadow/get/rejected"
        ]
      }
    ]
  })
}

# attach policy to each device certificate
resource "aws_iot_policy_attachment" "devices" {
  for_each = toset(var.devices)
  policy   = aws_iot_policy.device.name
  target   = aws_iot_certificate.devices[each.value].arn
}

# attach each certificate to its Thing
resource "aws_iot_thing_principal_attachment" "devices" {
  for_each  = toset(var.devices)
  thing     = aws_iot_thing.devices[each.value].name
  principal = aws_iot_certificate.devices[each.value].arn
}

# IoT endpoint
data "aws_iot_endpoint" "ats" {
  endpoint_type = "iot:Data-ATS"
}

# save cert + key files locally per device
resource "local_file" "cert" {
  for_each        = toset(var.devices)
  content         = aws_iot_certificate.devices[each.value].certificate_pem
  filename        = "${path.root}/../certs/${each.value}/cert.pem"
  file_permission = "0600"
}

resource "local_file" "key" {
  for_each        = toset(var.devices)
  content         = tls_private_key.devices[each.value].private_key_pem
  filename        = "${path.root}/../certs/${each.value}/private.key"
  file_permission = "0600"
}

# download root CA
resource "null_resource" "root_ca" {
  provisioner "local-exec" {
    command = "curl -so ${path.root}/../certs/root-CA.crt https://www.amazontrust.com/repository/AmazonRootCA1.pem"
  }
  triggers = { always = timestamp() }
}
