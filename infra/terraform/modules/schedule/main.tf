# Daily EDGAR refresh: EventBridge Scheduler runs the api image once with the ingest CLI (`run`: the newest
# filing per form per ticker; the pinned demo corpus is `cli demo`, a one-off).
resource "aws_iam_role" "scheduler" {
  name = "${var.project}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "ecs:RunTask"
        Resource  = var.task_definition_arn
        Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } }
      },
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = var.pass_role_arns
      },
    ]
  })
}

resource "aws_scheduler_schedule" "ingest" {
  name                         = "${var.project}-ingest-daily"
  schedule_expression          = "cron(0 6 * * ? *)" # 06:00 UTC, after EDGAR's overnight index refresh
  schedule_expression_timezone = "UTC"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = var.cluster_arn
    role_arn = aws_iam_role.scheduler.arn
    ecs_parameters {
      task_definition_arn = var.task_definition_arn
      launch_type         = "FARGATE"
      task_count          = 1
      network_configuration {
        subnets          = var.subnet_ids
        security_groups  = [var.security_group_id]
        assign_public_ip = true # public subnets, no NAT (modules/network)
      }
    }
    input = jsonencode({
      containerOverrides = [{
        name    = var.container_name
        command = ["python", "-m", "finance_agents.ingest.cli", "run", "--tickers", "AAPL,MSFT", "--forms", "10-K,8-K", "--limit", "1"]
      }]
    })
  }
}
