# ADOT sidecar for the api task: scrapes :8000/metrics into CloudWatch EMF and forwards OTLP
# traces to Phoenix. The container definition is returned to the compute module, which places it.
resource "aws_cloudwatch_log_group" "adot" {
  name              = "/${var.project}/adot"
  retention_in_days = 14
}

locals {
  adot_config = templatefile("${path.module}/adot.yaml", {
    project          = var.project
    region           = var.region
    phoenix_endpoint = var.phoenix_endpoint
  })
  adot_container = {
    name      = "adot"
    image     = "public.ecr.aws/aws-observability/aws-otel-collector:v0.40.0"
    essential = false
    environment = [
      { name = "AOT_CONFIG_CONTENT", value = local.adot_config },
    ]
    portMappings = [{ containerPort = 4318, protocol = "tcp" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.adot.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "adot"
      }
    }
  }
}

# In production: a Grafana (managed or self-hosted) with CloudWatch + Prometheus datasources and
#   infra/grafana/api-dashboard.json provisioned.
# In this demo: off by default, because Amazon Managed Grafana bills ~$9/editor/month and the
#   CloudWatch console shows the same EMF metrics; flip enable_grafana=true to create it.
resource "aws_grafana_workspace" "this" {
  count                    = var.enable_grafana ? 1 : 0
  name                     = var.project
  account_access_type      = "CURRENT_ACCOUNT"
  authentication_providers = ["AWS_SSO"]
  permission_type          = "SERVICE_MANAGED"
  data_sources             = ["CLOUDWATCH", "PROMETHEUS"]
  role_arn                 = aws_iam_role.grafana[0].arn
}

resource "aws_iam_role" "grafana" {
  count = var.enable_grafana ? 1 : 0
  name  = "${var.project}-grafana"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "grafana.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "grafana_cloudwatch" {
  count      = var.enable_grafana ? 1 : 0
  role       = aws_iam_role.grafana[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonGrafanaCloudWatchAccess"
}
