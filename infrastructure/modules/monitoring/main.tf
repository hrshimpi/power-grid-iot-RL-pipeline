resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${var.prefix}-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_description   = "Lambda error rate high"
  dimensions          = { FunctionName = var.lambda_function_name }
  alarm_actions       = [var.sns_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "lambda_duration" {
  alarm_name          = "${var.prefix}-lambda-duration"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "Duration"
  namespace           = "AWS/Lambda"
  period              = 60
  extended_statistic  = "p99"
  threshold           = 5000
  treat_missing_data  = "notBreaching"
  alarm_description   = "Lambda p99 duration > 5s"
  dimensions          = { FunctionName = var.lambda_function_name }
  alarm_actions       = [var.sns_topic_arn]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.prefix}-overview"
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title   = "Lambda invocations"
          region  = var.region
          period  = 60
          stat    = "Sum"
          metrics = [["AWS/Lambda", "Invocations", "FunctionName", var.lambda_function_name]]
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title   = "Lambda errors"
          region  = var.region
          period  = 60
          stat    = "Sum"
          metrics = [["AWS/Lambda", "Errors", "FunctionName", var.lambda_function_name]]
        }
      }
    ]
  })
}
