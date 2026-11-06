# Event-driven intake and notifications (serverless). Design: docs/adr/0009-event-driven-intake-and-notifications.md
#
#   in:  S3 intake bucket ─Object Created─▶ default bus rule ─▶ SQS (+DLQ) ─▶ intake Lambda ─▶ POST /api/batches
#   out: outbox relay (ECS) ─PutEvents─▶ custom bus ─rule─▶ notifier Lambda ─▶ chat webhook   (+ DLQ, + 7-day archive)
#
# Everything here is pay-per-use: idle cost is ~0 (one small Fargate task for the relay aside).

variable "enable_events" {
  description = "Create the S3/EventBridge/SQS/Lambda intake and the notification path"
  type        = bool
  default     = true
}

variable "intake_default_credential_id" {
  description = "Vault credential id the intake Lambda uses when a dropped file does not name one (x-amz-meta-credential-id)"
  type        = string
  default     = ""
}

variable "intake_approval_threshold" {
  description = "Claims above this amount from S3 intake wait for a human approval. Empty: no threshold."
  type        = string
  default     = ""
}

variable "notify_webhook_url" {
  description = "Slack/Teams incoming-webhook URL for operator notifications. Empty: the notifier only logs."
  type        = string
  default     = ""
  sensitive   = true
}

locals {
  ev        = var.enable_events ? 1 : 0
  lambda_rt = "python3.12"
}

# ------------------------------------------------------------------ custom event bus + archive
resource "aws_cloudwatch_event_bus" "main" {
  count = local.ev
  name  = local.name
}

resource "aws_cloudwatch_event_archive" "main" {
  count            = local.ev
  name             = "${local.name}-all"
  event_source_arn = aws_cloudwatch_event_bus.main[0].arn
  retention_days   = 7 # replay window after a consumer bug
}

# ------------------------------------------------------------------ intake bucket
resource "aws_s3_bucket" "intake" {
  count         = local.ev
  bucket_prefix = "${local.name}-intake-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "intake" {
  count                   = local.ev
  bucket                  = aws_s3_bucket.intake[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "intake" {
  count  = local.ev
  bucket = aws_s3_bucket.intake[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "intake" {
  count  = local.ev
  bucket = aws_s3_bucket.intake[0].id
  rule {
    id     = "expire-claim-files"
    status = "Enabled"
    filter {}
    expiration { days = 30 } # CSVs hold PII; the batch keeps the parsed rows
  }
}

resource "aws_s3_bucket_notification" "intake" {
  count       = local.ev
  bucket      = aws_s3_bucket.intake[0].id
  eventbridge = true
}

# ------------------------------------------------------------------ intake queue
resource "aws_sqs_queue" "intake_dlq" {
  count                     = local.ev
  name                      = "${local.name}-intake-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "intake" {
  count                      = local.ev
  name                       = "${local.name}-intake"
  visibility_timeout_seconds = 180 # 6x the function timeout
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.intake_dlq[0].arn
    maxReceiveCount     = 3
  })
}

resource "aws_cloudwatch_event_rule" "intake" {
  count       = local.ev
  name        = "${local.name}-intake-csv"
  description = "CSV dropped under incoming/ in the intake bucket"
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.intake[0].bucket] }
      object = { key = [{ prefix = "incoming/" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "intake" {
  count = local.ev
  rule  = aws_cloudwatch_event_rule.intake[0].name
  arn   = aws_sqs_queue.intake[0].arn
}

resource "aws_sqs_queue_policy" "intake" {
  count     = local.ev
  queue_url = aws_sqs_queue.intake[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sqs:SendMessage",
    Resource  = aws_sqs_queue.intake[0].arn,
    Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.intake[0].arn } }
  }] })
}

# ------------------------------------------------------------------ Lambda packaging + roles
data "archive_file" "intake" {
  count       = local.ev
  type        = "zip"
  source_file = "${path.module}/../../serverless/intake/handler.py"
  output_path = "${path.module}/.build/intake.zip"
}

data "archive_file" "notifier" {
  count       = local.ev
  type        = "zip"
  source_file = "${path.module}/../../serverless/notifier/handler.py"
  output_path = "${path.module}/.build/notifier.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "intake" {
  count              = local.ev
  name_prefix        = "${local.name}-intake-"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "intake" {
  count = local.ev
  role  = aws_iam_role.intake[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${aws_s3_bucket.intake[0].arn}/incoming/*"] },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${aws_s3_bucket.intake[0].arn}/rejected/*"] },
    {
      Effect   = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
      Resource = [aws_sqs_queue.intake[0].arn]
    },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [aws_secretsmanager_secret.app.arn] },
    {
      Effect   = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
      Resource = ["${aws_cloudwatch_log_group.lambda["intake"].arn}:*"]
    },
  ] })
}

resource "aws_iam_role" "notifier" {
  count              = local.ev
  name_prefix        = "${local.name}-notify-"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "notifier" {
  count = local.ev
  role  = aws_iam_role.notifier[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    {
      Effect   = "Allow", Action = ["dynamodb:PutItem", "dynamodb:DeleteItem"],
      Resource = [aws_dynamodb_table.notify_dedupe[0].arn]
    },
    {
      Effect   = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
      Resource = ["${aws_cloudwatch_log_group.lambda["notifier"].arn}:*"]
    },
    ], var.notify_webhook_url == "" ? [] : [
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [aws_secretsmanager_secret.webhook[0].arn] }
  ]) })
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = var.enable_events ? toset(["intake", "notifier"]) : toset([])
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = var.log_retention_days
}

# ------------------------------------------------------------------ intake Lambda
resource "aws_lambda_function" "intake" {
  count            = local.ev
  function_name    = "${local.name}-intake"
  role             = aws_iam_role.intake[0].arn
  runtime          = local.lambda_rt
  handler          = "handler.handler"
  filename         = data.archive_file.intake[0].output_path
  source_code_hash = data.archive_file.intake[0].output_base64sha256
  timeout          = 30
  memory_size      = 256
  # The API is reached through the ALB, so the function needs no VPC (and no NAT for Secrets Manager).
  # A client whose API is internal-only puts it in the VPC with endpoints instead (ADR 0009).
  environment {
    variables = {
      API_URL               = "${local.https ? "https" : "http"}://${aws_lb.main.dns_name}"
      APP_SECRET_ARN        = aws_secretsmanager_secret.app.arn
      DEFAULT_CREDENTIAL_ID = var.intake_default_credential_id
      APPROVAL_THRESHOLD    = var.intake_approval_threshold
    }
  }
  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_event_source_mapping" "intake" {
  count                   = local.ev
  event_source_arn        = aws_sqs_queue.intake[0].arn
  function_name           = aws_lambda_function.intake[0].arn
  batch_size              = 5
  function_response_types = ["ReportBatchItemFailures"]
  scaling_config { maximum_concurrency = 2 } # the API and the portal are the bottleneck, not Lambda
}

# ------------------------------------------------------------------ notifier Lambda
resource "aws_dynamodb_table" "notify_dedupe" {
  count        = local.ev
  name         = "${local.name}-notify-dedupe"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"
  attribute {
    name = "event_id"
    type = "S"
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

resource "aws_secretsmanager_secret" "webhook" {
  count                   = var.enable_events && var.notify_webhook_url != "" ? 1 : 0
  name_prefix             = "${local.name}-notify-webhook-"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "webhook" {
  count         = length(aws_secretsmanager_secret.webhook)
  secret_id     = aws_secretsmanager_secret.webhook[0].id
  secret_string = var.notify_webhook_url
}

resource "aws_lambda_function" "notifier" {
  count            = local.ev
  function_name    = "${local.name}-notifier"
  role             = aws_iam_role.notifier[0].arn
  runtime          = local.lambda_rt
  handler          = "handler.handler"
  filename         = data.archive_file.notifier[0].output_path
  source_code_hash = data.archive_file.notifier[0].output_base64sha256
  timeout          = 15
  memory_size      = 128
  environment {
    variables = {
      DEDUPE_TABLE       = aws_dynamodb_table.notify_dedupe[0].name
      WEBHOOK_SECRET_ARN = var.notify_webhook_url == "" ? "" : aws_secretsmanager_secret.webhook[0].arn
      STUDIO_URL         = "${local.https ? "https" : "http"}://${aws_lb.main.dns_name}"
    }
  }
  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_sqs_queue" "notify_dlq" {
  count                     = local.ev
  name                      = "${local.name}-notify-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue_policy" "notify_dlq" {
  count     = local.ev
  queue_url = aws_sqs_queue.notify_dlq[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sqs:SendMessage",
    Resource  = aws_sqs_queue.notify_dlq[0].arn,
    Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.notify[0].arn } }
  }] })
}

resource "aws_cloudwatch_event_rule" "notify" {
  count          = local.ev
  name           = "${local.name}-notify-operators"
  event_bus_name = aws_cloudwatch_event_bus.main[0].name
  event_pattern = jsonencode({
    source      = ["portalpilot"]
    detail-type = ["intervention.opened", "incident.opened", "task.failed", "batch.finished"]
  })
}

resource "aws_cloudwatch_event_target" "notify" {
  count          = local.ev
  rule           = aws_cloudwatch_event_rule.notify[0].name
  event_bus_name = aws_cloudwatch_event_bus.main[0].name
  arn            = aws_lambda_function.notifier[0].arn
  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 8
  }
  dead_letter_config { arn = aws_sqs_queue.notify_dlq[0].arn }
}

resource "aws_lambda_permission" "notify" {
  count         = local.ev
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notifier[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.notify[0].arn
}

# ------------------------------------------------------------------ outbox relay (ECS)
resource "aws_cloudwatch_log_group" "relay" {
  count             = local.ev
  name              = "/portalpilot/${var.client}/relay"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "relay" {
  count              = local.ev
  name_prefix        = "${local.name}-relay-"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "relay" {
  count = local.ev
  role  = aws_iam_role.relay[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["events:PutEvents"], Resource = [aws_cloudwatch_event_bus.main[0].arn] }
  ] })
}

resource "aws_ecs_task_definition" "relay" {
  count                    = local.ev
  family                   = "${local.name}-relay"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.relay[0].arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name    = "relay", image = local.repo["backend"], essential = true
    command = ["python", "-m", "portalpilot.events.relay"]
    environment = concat(local.app_env, [
      { name = "EVENT_SINK", value = "eventbridge" },
      { name = "EVENT_BUS_NAME", value = aws_cloudwatch_event_bus.main[0].name },
    ])
    secrets = local.app_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = { "awslogs-group" = aws_cloudwatch_log_group.relay[0].name, "awslogs-region" = var.region,
      "awslogs-stream-prefix" = "relay" }
    }
  }])
}

resource "aws_ecs_service" "relay" {
  count           = local.ev
  name            = "relay"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.relay[0].arn
  desired_count   = 1 # a second replica would stand by on the advisory lock
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = local.network.subnets
    security_groups  = local.network.security_groups
    assign_public_ip = local.network.assign_public_ip
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_secretsmanager_secret_version.app]
}

# ------------------------------------------------------------------ alarms: anything in a DLQ needs a human
resource "aws_cloudwatch_metric_alarm" "dlq" {
  for_each = var.enable_events ? {
    intake   = aws_sqs_queue.intake_dlq[0].name
    notifier = aws_sqs_queue.notify_dlq[0].name
  } : {}
  alarm_name          = "${local.name}-${each.key}-dlq"
  alarm_description   = "Messages in the ${each.key} dead-letter queue. Runbook: docs/ops/runbooks/dlq-not-empty.md"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = each.value }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "intake_errors" {
  count               = local.ev
  alarm_name          = "${local.name}-intake-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.intake[0].function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

output "intake_bucket" {
  description = "Drop claim CSVs at s3://<this>/incoming/ to start a batch"
  value       = var.enable_events ? aws_s3_bucket.intake[0].bucket : null
}

output "event_bus" {
  value = var.enable_events ? aws_cloudwatch_event_bus.main[0].name : null
}
