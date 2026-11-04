# ------------------------------------------------------------------ alarms
resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.name}-api-5xx"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = aws_lb.main.arn_suffix, TargetGroup = aws_lb_target_group.api.arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "no_workers" {
  alarm_name          = "${local.name}-no-workers"
  alarm_description   = "No browser workers running: workflows will queue (safely) until one returns"
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  dimensions          = { ClusterName = aws_ecs_cluster.main.name, ServiceName = aws_ecs_service.worker.name }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# ------------------------------------------------------------------ outputs
output "console_url" {
  value = "${local.https ? "https" : "http"}://${aws_lb.main.dns_name}"
}

output "mock_portal_url" {
  value = var.deploy_mock_portal ? "http://${aws_lb.main.dns_name}:8080" : null
}

output "ecr" {
  value = { for k, r in aws_ecr_repository.repo : k => r.repository_url }
}

output "operator_token_secret" {
  description = "aws secretsmanager get-secret-value --secret-id <this> | jq -r .SecretString | jq -r .OPERATOR_TOKEN"
  value       = aws_secretsmanager_secret.app.name
}

output "service_namespace" {
  value = local.ns
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}
