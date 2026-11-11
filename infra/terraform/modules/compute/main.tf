# ECS cluster + Fargate services: api (behind the ALB), temporal, phoenix, ingest-worker.
# Services find each other by name through ECS Service Connect (temporal:7233, phoenix:6006).
locals {
  api_container = "api"
  log_groups    = toset(["api", "temporal", "phoenix", "ingest-worker"])
  model_arns    = [for id in values(var.model_ids) : "arn:aws:bedrock:${var.region}::foundation-model/${id}"]

  app_environment = [
    { name = "HR_MODE", value = "aws" },
    { name = "HR_JWT_MODE", value = "cognito" },
    { name = "HR_CORS_ORIGINS", value = var.web_origin },
    { name = "COGNITO_USER_POOL_ID", value = var.cognito_user_pool_id },
    { name = "COGNITO_REGION", value = var.region },
    { name = "COGNITO_APP_CLIENT_ID", value = var.cognito_client_id },
    { name = "HR_AUTHZ_BACKEND", value = "verified_permissions" },
    { name = "AVP_POLICY_STORE_ID", value = var.avp_policy_store_id },
    { name = "HR_MODEL_BACKEND", value = "bedrock" },
    { name = "AWS_REGION", value = var.region },
    { name = "BEDROCK_GENERATION_MODEL", value = var.model_ids.generation },
    { name = "BEDROCK_VERIFIER_MODEL", value = var.model_ids.verifier },
    { name = "BEDROCK_EMBEDDING_MODEL", value = var.model_ids.embedding },
    { name = "BEDROCK_RERANK_MODEL", value = var.model_ids.rerank },
    { name = "HR_EMBED_BACKEND", value = "bedrock" },
    { name = "HR_RERANK_BACKEND", value = "bedrock" },
    { name = "REDIS_URL", value = var.redis_url },
    { name = "TEMPORAL_ADDRESS", value = "temporal:7233" },
    { name = "TEMPORAL_NAMESPACE", value = "default" },
    { name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = "http://localhost:4318" }, # the ADOT sidecar
    { name = "OTEL_SERVICE_NAME", value = "${var.project}-api" },
    { name = "PHOENIX_COLLECTOR_ENDPOINT", value = "http://phoenix:6006" },
  ]
  app_secrets = [{ name = "DATABASE_URL", valueFrom = "${var.db_secret_arn}:url::" }]
}

resource "aws_ecs_cluster" "this" {
  name = var.project
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_service_discovery_http_namespace" "this" {
  name = var.project
}

resource "aws_cloudwatch_log_group" "svc" {
  for_each          = local.log_groups
  name              = "/${var.project}/${each.value}"
  retention_in_days = 14
}

resource "aws_ecr_repository" "this" {
  for_each             = toset(["api", "web"])
  name                 = "${var.project}-${each.value}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # teardown.sh empties it first; this covers a plain `destroy`
  image_scanning_configuration {
    scan_on_push = true
  }
}

# ---------------- IAM ----------------
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: pull images, write logs, inject the DB secret into containers.
resource "aws_iam_role" "execution" {
  name               = "${var.project}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secret" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = var.db_secret_arn
    }]
  })
}

# Task role for api + ingest-worker: only the four models, the one policy store, the one secret.
resource "aws_iam_role" "app" {
  name               = "${var.project}-app"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "app" {
  role = aws_iam_role.app.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = local.model_arns
      },
      {
        Effect   = "Allow"
        Action   = ["verifiedpermissions:IsAuthorized", "verifiedpermissions:IsAuthorizedWithToken"]
        Resource = var.avp_policy_store_arn
      },
      {
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = var.db_secret_arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = "arn:aws:logs:${var.region}:*:log-group:/${var.project}/*"
      },
      {
        Effect    = "Allow"
        Action    = "cloudwatch:PutMetricData" # the ADOT sidecar's EMF exporter
        Resource  = "*"
        Condition = { StringEquals = { "cloudwatch:namespace" = var.project } }
      },
    ]
  })
}

# ---------------- ALB ----------------
resource "aws_lb" "this" {
  name               = var.project
  load_balancer_type = "application"
  security_groups    = [var.alb_security_group_id]
  subnets            = var.subnet_ids
}

resource "aws_lb_target_group" "api" {
  name        = "${var.project}-api"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id
  health_check {
    path     = "/healthz"
    matcher  = "200"
    interval = 15
  }
  deregistration_delay = 15
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# ---------------- task definitions ----------------
locals {
  log_config = { for g in local.log_groups : g => {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.svc[g].name
      awslogs-region        = var.region
      awslogs-stream-prefix = g
    }
  } }
  api_image = "${aws_ecr_repository.this["api"].repository_url}:${var.image_tag}"
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.project}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.app.arn
  container_definitions = jsonencode(concat([{
    name      = local.api_container
    image     = local.api_image
    essential = true
    # Migrations run on boot; db.run_migrations takes an advisory lock so two replicas cannot race.
    command          = ["sh", "-c", "python -m harness_rag.ingest.cli migrate && uvicorn harness_rag.api.app:app --host 0.0.0.0 --port 8000"]
    portMappings     = [{ name = "http", containerPort = 8000, protocol = "tcp" }]
    environment      = local.app_environment
    secrets          = local.app_secrets
    logConfiguration = local.log_config["api"]
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)\""]
      interval    = 15
      timeout     = 3
      retries     = 5
      startPeriod = 60
    }
  }], var.extra_api_containers))
}

resource "aws_ecs_task_definition" "ingest_worker" {
  family                   = "${var.project}-ingest-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.app.arn
  container_definitions = jsonencode([{
    name             = "ingest-worker"
    image            = local.api_image
    essential        = true
    command          = ["python", "-m", "harness_rag.ingest.cli", "worker"]
    environment      = local.app_environment
    secrets          = local.app_secrets
    logConfiguration = local.log_config["ingest-worker"]
  }])
}

# In production: Temporal Cloud, or a multi-service Temporal cluster with its own Postgres.
# In this demo: the single-binary auto-setup image with `--db postgres12` persistence on the same
#   RDS instance (databases `temporal` / `temporal_visibility`), because one t4g.small carries both
#   and workflow history still survives a task restart, unlike the compose dev server.
resource "aws_ecs_task_definition" "temporal" {
  family                   = "${var.project}-temporal"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name         = "temporal"
    image        = "temporalio/auto-setup:1.25.2"
    essential    = true
    portMappings = [{ name = "grpc", containerPort = 7233, protocol = "tcp" }]
    environment = [
      { name = "DB", value = "postgres12" },
      { name = "DB_PORT", value = "5432" },
      { name = "POSTGRES_SEEDS", value = var.db_address },
      { name = "POSTGRES_USER", value = var.db_username },
      { name = "DBNAME", value = "temporal" },
      { name = "VISIBILITY_DBNAME", value = "temporal_visibility" },
      { name = "SQL_TLS_ENABLED", value = "true" }, # rds.force_ssl=1 in modules/database
      { name = "SQL_TLS", value = "true" },
      { name = "SQL_TLS_DISABLE_HOST_VERIFICATION", value = "true" },
      { name = "BIND_ON_IP", value = "0.0.0.0" },
      { name = "TEMPORAL_BROADCAST_ADDRESS", value = "temporal" },
    ]
    secrets          = [{ name = "POSTGRES_PWD", valueFrom = "${var.db_secret_arn}:password::" }]
    logConfiguration = local.log_config["temporal"]
  }])
}

resource "aws_ecs_task_definition" "phoenix" {
  family                   = "${var.project}-phoenix"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name             = "phoenix"
    image            = "arizephoenix/phoenix:latest"
    essential        = true
    portMappings     = [{ name = "http", containerPort = 6006, protocol = "tcp" }]
    logConfiguration = local.log_config["phoenix"]
  }])
}

# ---------------- services ----------------
resource "aws_ecs_service" "api" {
  name                   = "api"
  cluster                = aws_ecs_cluster.this.id
  task_definition        = aws_ecs_task_definition.api.arn
  desired_count          = 1
  launch_type            = "FARGATE"
  enable_execute_command = true
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.service_security_group_id]
    assign_public_ip = true # public subnets, no NAT (modules/network)
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = local.api_container
    container_port   = 8000
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn
  }
  lifecycle {
    ignore_changes = [desired_count] # autoscaling owns it
  }
  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "ingest_worker" {
  name            = "ingest-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.ingest_worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.service_security_group_id]
    assign_public_ip = true
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn
  }
}

resource "aws_ecs_service" "temporal" {
  name            = "temporal"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.temporal.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.service_security_group_id]
    assign_public_ip = true
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn
    service {
      port_name      = "grpc"
      discovery_name = "temporal"
      client_alias {
        port     = 7233
        dns_name = "temporal"
      }
    }
  }
}

resource "aws_ecs_service" "phoenix" {
  name            = "phoenix"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.phoenix.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.service_security_group_id]
    assign_public_ip = true
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn
    service {
      port_name      = "http"
      discovery_name = "phoenix"
      client_alias {
        port     = 6006
        dns_name = "phoenix"
      }
    }
  }
}

# ---------------- api autoscaling 1-4 on CPU ----------------
resource "aws_appautoscaling_target" "api" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 1
  max_capacity       = 4
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${var.project}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
