resource "aws_secretsmanager_secret" "hmac" {
  name                    = "${var.prefix}/hmac-secret"
  description             = "Shared HMAC secret for payload signing between edge and Lambda"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "hmac" {
  secret_id     = aws_secretsmanager_secret.hmac.id
  secret_string = jsonencode({ hmac_secret = var.hmac_secret })
}
