output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "api_task_definition_arn" {
  value = aws_ecs_task_definition.api.arn
}

output "api_container_name" {
  value = local.api_container
}

output "task_role_arn" {
  value = aws_iam_role.app.arn
}

output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}

output "ecr_repository_urls" {
  value = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "ecr_repository_names" {
  value = [for r in aws_ecr_repository.this : r.name]
}

output "phoenix_url" {
  description = "Reachable only inside the cluster (Service Connect); open it with `aws ecs execute-command` port-forwarding."
  value       = "http://phoenix:6006"
}
