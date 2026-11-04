data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: what ECS needs to start the containers (pull images, write logs, read the injected secrets).
resource "aws_iam_role" "exec" {
  name_prefix        = "${local.name}-exec-"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "exec" {
  role       = aws_iam_role.exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "exec_secrets" {
  role = aws_iam_role.exec.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [aws_secretsmanager_secret.app.arn]
  }] })
}

# Task role: what the API and worker code may do. Least privilege: one bucket, one KMS key, nothing else.
resource "aws_iam_role" "app" {
  name_prefix        = "${local.name}-app-"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "app" {
  role = aws_iam_role.app.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    {
      Effect   = "Allow", Action = ["s3:GetObject", "s3:PutObject"],
      Resource = ["${aws_s3_bucket.screens.arn}/*"]
    },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [aws_s3_bucket.screens.arn] },
    {
      Effect   = "Allow", Action = ["kms:GenerateDataKey", "kms:Decrypt"],
      Resource = [aws_kms_key.vault.arn]
    },
  ] })
}
