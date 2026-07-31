variable "prefix" { type = string }

variable "hmac_secret" {
  type      = string
  sensitive = true
}
