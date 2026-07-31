provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.env
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" { state = "available" }

locals {
  account_id = data.aws_caller_identity.current.account_id
  prefix     = "${var.project}-${var.env}"
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)
}

# ── VPC ───────────────────────────────────────────────────────
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.prefix}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.prefix}-igw" }
}

# public subnets (for NAT gateway)
resource "aws_subnet" "public" {
  count                   = length(var.public_subnet_cidrs)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.prefix}-public-${count.index + 1}" }
}

# private subnets (Lambda runs here)
resource "aws_subnet" "private" {
  count             = length(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index]
  tags              = { Name = "${local.prefix}-private-${count.index + 1}" }
}

# NAT gateway (allows Lambda in private subnet to reach AWS APIs)
resource "aws_eip" "nat" {
  count  = 1
  domain = "vpc"
  tags   = { Name = "${local.prefix}-nat-eip" }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = "${local.prefix}-nat" }
  depends_on    = [aws_internet_gateway.main]
}

# route tables
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.prefix}-rt-public" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = { Name = "${local.prefix}-rt-private" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Lambda security group
resource "aws_security_group" "lambda" {
  name        = "${local.prefix}-lambda-sg"
  description = "Lambda function security group"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS to AWS APIs"
  }

  tags = { Name = "${local.prefix}-lambda-sg" }
}

# VPC endpoints to avoid NAT for S3 and Secrets Manager
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  tags              = { Name = "${local.prefix}-s3-endpoint" }
}

resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.lambda.id]
  private_dns_enabled = true
  tags                = { Name = "${local.prefix}-secrets-endpoint" }
}

# ── secrets ───────────────────────────────────────────────────
module "secrets" {
  source      = "./modules/secrets"
  prefix      = local.prefix
  hmac_secret = var.hmac_secret
}

# ── storage ───────────────────────────────────────────────────
module "storage" {
  source        = "./modules/storage"
  prefix        = local.prefix
  force_destroy = var.s3_force_destroy
}

# ── alerting ─────────────────────────────────────────────────
module "alerting" {
  source      = "./modules/alerting"
  prefix      = local.prefix
  alert_email = var.alert_email
}

# ── iot ──────────────────────────────────────────────────────
module "iot" {
  source     = "./modules/iot"
  prefix     = local.prefix
  devices    = var.devices
  account_id = local.account_id
  region     = var.aws_region
}

# ── iam ──────────────────────────────────────────────────────
module "iam" {
  source             = "./modules/iam"
  prefix             = local.prefix
  account_id         = local.account_id
  region             = var.aws_region
  s3_bucket_arn      = module.storage.bucket_arn
  sns_topic_arn      = module.alerting.topic_arn
  hmac_secret_arn    = module.secrets.hmac_secret_arn
  sagemaker_endpoint = var.sagemaker_endpoint
}

# ── lambda ───────────────────────────────────────────────────
module "lambda" {
  source             = "./modules/lambda"
  prefix             = local.prefix
  account_id         = local.account_id
  role_arn           = module.iam.lambda_role_arn
  timeout            = var.lambda_timeout
  memory             = var.lambda_memory
  s3_bucket_name     = module.storage.bucket_name
  sns_topic_arn      = module.alerting.topic_arn
  hmac_secret_arn    = module.secrets.hmac_secret_arn
  sagemaker_endpoint = var.sagemaker_endpoint
  vpc_id             = aws_vpc.main.id
  private_subnet_ids = aws_subnet.private[*].id
  lambda_sg_id       = aws_security_group.lambda.id
}

# ── IoT rule (routes messages to Lambda) ─────────────────────
resource "aws_iot_topic_rule" "to_lambda" {
  name        = "${replace(local.prefix, "-", "_")}_to_lambda"
  description = "Route grid telemetry to inference Lambda"
  enabled     = true
  sql         = "SELECT * FROM 'grid/+/telemetry'"
  sql_version = "2016-03-23"

  lambda {
    function_arn = module.lambda.function_arn
  }

  error_action {
    cloudwatch_metric {
      role_arn         = module.iam.iot_rule_error_action_role_arn
      metric_name      = "IoTRuleErrors"
      metric_namespace = "GridIoTRL"
      metric_value     = "1"
      metric_unit      = "Count"
    }
  }
}

# allow this specific IoT rule (and no other) to invoke the Lambda
resource "aws_lambda_permission" "iot" {
  statement_id  = "AllowIoTRuleInvoke"
  action        = "lambda:InvokeFunction"
  function_name = module.lambda.function_name
  principal     = "iot.amazonaws.com"
  source_arn    = aws_iot_topic_rule.to_lambda.arn
}

# ── monitoring ───────────────────────────────────────────────
module "monitoring" {
  source               = "./modules/monitoring"
  prefix               = local.prefix
  lambda_function_name = module.lambda.function_name
  sns_topic_arn        = module.alerting.topic_arn
}
