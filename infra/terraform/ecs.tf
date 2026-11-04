# ------------------------------------------------------------------ images
resource "aws_ecr_repository" "repo" {
  for_each             = toset(["backend", "portal", "web"])
  name                 = "${local.name}/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "repo" {
  for_each   = aws_ecr_repository.repo
  repository = each.value.name
  policy = jsonencode({ rules = [{
    rulePriority = 1, description = "keep 10 images",
    selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 },
    action       = { type = "expire" }
  }] })
}

# ------------------------------------------------------------------ cluster + discovery
resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_service_discovery_private_dns_namespace" "ns" {
  name = local.ns
  vpc  = aws_vpc.main.id
}

resource "aws_service_discovery_service" "svc" {
  for_each = toset(concat(["api"], local.self_host_temporal ? ["temporal"] : [], var.deploy_mock_portal ? ["portal"] : []))
  name     = each.key
  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.ns.id
    routing_policy = "MULTIVALUE"
    dns_records {
      ttl  = 10
      type = "A"
    }
  }
}

resource "aws_cloudwatch_log_group" "svc" {
  for_each          = toset(["api", "worker", "temporal", "portal", "web"])
  name              = "/portalpilot/${var.client}/${each.key}"
  retention_in_days = var.log_retention_days
}

locals {
  self_host_temporal = var.temporal_address == ""
  repo               = { for k, r in aws_ecr_repository.repo : k => "${r.repository_url}:${var.image_tag}" }
  app_env = [
    { name = "TEMPORAL_ADDRESS", value = local.self_host_temporal ? "temporal.${local.ns}:7233" : var.temporal_address },
    { name = "S3_BUCKET", value = aws_s3_bucket.screens.bucket },
    { name = "AWS_REGION", value = var.region },
    { name = "KEY_PROVIDER", value = "kms" },
    { name = "KMS_KEY_ID", value = aws_kms_key.vault.arn },
    { name = "GROUNDER_CHAIN", value = var.grounder_chain },
    { name = "QWEN_URL", value = var.qwen_url },
    { name = "PORTAL_URL", value = var.deploy_mock_portal ? "http://portal.${local.ns}:8080" : var.portal_url },
  ]
  app_secrets = [for k in ["DATABASE_URL", "OPERATOR_TOKEN", "ANTHROPIC_API_KEY"] :
  { name = k, valueFrom = "${aws_secretsmanager_secret.app.arn}:${k}::" }]
  log = { for k in ["api", "worker", "temporal", "portal", "web"] : k => {
    logDriver = "awslogs"
    options = { "awslogs-group" = aws_cloudwatch_log_group.svc[k].name, "awslogs-region" = var.region,
    "awslogs-stream-prefix" = k }
  } }
  network = {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true
  }
}

# ------------------------------------------------------------------ task definitions
resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.app.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name             = "api", image = local.repo["backend"], essential = true
    portMappings     = [{ containerPort = 8000 }]
    environment      = local.app_env
    secrets          = local.app_secrets
    logConfiguration = local.log["api"]
    healthCheck = { command = ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://localhost:8000/api/healthz')\""],
    interval = 15, retries = 3, startPeriod = 60, timeout = 5 }
  }])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.app.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name             = "worker", image = local.repo["backend"], essential = true
    command          = ["python", "-m", "portalpilot.orchestration.worker"]
    environment      = concat(local.app_env, [{ name = "MAX_BROWSER_SESSIONS", value = tostring(var.max_browser_sessions) }])
    secrets          = local.app_secrets
    logConfiguration = local.log["worker"]
    stopTimeout      = 60 # let in-flight activities finish; anything cut off is recovered by the workflow
  }])
}

resource "aws_ecs_task_definition" "temporal" {
  count                    = local.self_host_temporal ? 1 : 0
  family                   = "${local.name}-temporal"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.exec.arn
  container_definitions = jsonencode([{
    name         = "temporal", image = "temporalio/auto-setup:1.29.1", essential = true
    portMappings = [{ containerPort = 7233 }]
    environment = [
      { name = "DB", value = "postgres12" },
      { name = "DB_PORT", value = "5432" },
      { name = "POSTGRES_SEEDS", value = aws_db_instance.main.address },
      { name = "POSTGRES_USER", value = "portalpilot" },
    ]
    secrets          = [{ name = "POSTGRES_PWD", valueFrom = "${aws_secretsmanager_secret.app.arn}:DB_PASSWORD::" }]
    logConfiguration = local.log["temporal"]
  }])
}

resource "aws_ecs_task_definition" "portal" {
  count                    = var.deploy_mock_portal ? 1 : 0
  family                   = "${local.name}-portal"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.exec.arn
  container_definitions = jsonencode([{
    name             = "portal", image = local.repo["portal"], essential = true
    portMappings     = [{ containerPort = 8080 }]
    logConfiguration = local.log["portal"]
  }])
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.exec.arn
  container_definitions = jsonencode([{
    name             = "web", image = local.repo["web"], essential = true
    portMappings     = [{ containerPort = 80 }]
    logConfiguration = local.log["web"]
  }])
}

# ------------------------------------------------------------------ services
resource "aws_ecs_service" "temporal" {
  count           = local.self_host_temporal ? 1 : 0
  name            = "temporal"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.temporal[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = local.network.subnets
    security_groups  = local.network.security_groups
    assign_public_ip = local.network.assign_public_ip
  }
  service_registries { registry_arn = aws_service_discovery_service.svc["temporal"].arn }
}

resource "aws_ecs_service" "api" {
  name                              = "api"
  cluster                           = aws_ecs_cluster.main.id
  task_definition                   = aws_ecs_task_definition.api.arn
  desired_count                     = 1
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 90
  network_configuration {
    subnets          = local.network.subnets
    security_groups  = local.network.security_groups
    assign_public_ip = local.network.assign_public_ip
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  service_registries { registry_arn = aws_service_discovery_service.svc["api"].arn }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_lb_listener.http, aws_secretsmanager_secret_version.app]
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_count
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

resource "aws_ecs_service" "portal" {
  count           = var.deploy_mock_portal ? 1 : 0
  name            = "portal"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.portal[0].arn
  desired_count   = 1 # in-memory state: exactly one replica
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = local.network.subnets
    security_groups  = local.network.security_groups
    assign_public_ip = local.network.assign_public_ip
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.portal[0].arn
    container_name   = "portal"
    container_port   = 8080
  }
  service_registries { registry_arn = aws_service_discovery_service.svc["portal"].arn }
  depends_on = [aws_lb_listener.portal]
}

resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = local.network.subnets
    security_groups  = local.network.security_groups
    assign_public_ip = local.network.assign_public_ip
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 80
  }
  depends_on = [aws_lb_listener.http, aws_ecs_service.api]
}
