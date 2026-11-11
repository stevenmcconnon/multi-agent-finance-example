output "adot_container" {
  description = "Sidecar container definition to append to the api task."
  value       = local.adot_container
}

output "grafana_url" {
  value = var.enable_grafana ? "https://${aws_grafana_workspace.this[0].endpoint}" : null
}
