# ------------------------------------------------------------------ Postgres (app + self-hosted Temporal)
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

# Temporal's auto-setup image cannot negotiate TLS to Postgres, and RDS for PostgreSQL 15+ forces SSL by default.
# The app still connects with sslmode=require; the database is reachable only from the app security group inside
# the VPC. With Temporal Cloud (var.temporal_address) this parameter group can go.
resource "aws_db_parameter_group" "pg" {
  name   = "${local.name}-pg16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "0"
  }
}

resource "aws_db_instance" "main" {
  identifier                   = local.name
  engine                       = "postgres"
  engine_version               = "16"
  instance_class               = var.db_instance_class
  allocated_storage            = 20
  max_allocated_storage        = var.free_tier_account ? null : 100
  storage_encrypted            = true
  db_name                      = "portalpilot"
  username                     = "portalpilot"
  password                     = random_password.db.result
  db_subnet_group_name         = aws_db_subnet_group.main.name
  vpc_security_group_ids       = [aws_security_group.db.id]
  parameter_group_name         = aws_db_parameter_group.pg.name
  backup_retention_period      = var.free_tier_account ? 1 : 7
  deletion_protection          = var.db_deletion_protection
  skip_final_snapshot          = !var.db_deletion_protection
  final_snapshot_identifier    = var.db_deletion_protection ? "${local.name}-final" : null
  performance_insights_enabled = !var.free_tier_account
  apply_immediately            = true
}

# ------------------------------------------------------------------ screenshots
resource "aws_s3_bucket" "screens" {
  bucket_prefix = "${local.name}-screens-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "screens" {
  bucket                  = aws_s3_bucket.screens.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "screens" {
  bucket = aws_s3_bucket.screens.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "screens" {
  bucket = aws_s3_bucket.screens.id
  rule {
    id     = "expire-screens"
    status = "Enabled"
    filter {}
    expiration { days = 90 }
  }
}

# ------------------------------------------------------------------ credential vault master key
resource "aws_kms_key" "vault" {
  description             = "PortalPilot ${var.client} credential vault master key (wraps per-secret data keys)"
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_kms_alias" "vault" {
  name          = "alias/${local.name}-vault"
  target_key_id = aws_kms_key.vault.key_id
}

# ------------------------------------------------------------------ secrets
resource "random_password" "operator" {
  length  = 40
  special = false
}

resource "aws_secretsmanager_secret" "app" {
  name_prefix             = "${local.name}-app-"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    DATABASE_URL      = "postgresql://portalpilot:${random_password.db.result}@${aws_db_instance.main.address}:5432/portalpilot?sslmode=require"
    DB_PASSWORD       = random_password.db.result
    OPERATOR_TOKEN    = random_password.operator.result
    ANTHROPIC_API_KEY = var.anthropic_api_key
  })
}
