output "region" {
  value = var.region
}

output "alb_dns_name" {
  description = "Point the `api_domain` CNAME here."
  value       = module.compute.alb_dns_name
}

output "api_url" {
  value = "https://${var.api_domain}"
}

output "web_url" {
  value = module.web.url
}

output "web_bucket" {
  value = module.web.bucket
}

output "cognito_user_pool_id" {
  value = module.identity.user_pool_id
}

output "cognito_client_id" {
  value = module.identity.client_id
}

output "cognito_hosted_ui_domain" {
  value = module.identity.hosted_ui_domain
}

output "policy_store_id" {
  value = module.policy.policy_store_id
}

output "policy_ids" {
  value = module.policy.policy_ids
}

output "ecr_repository_urls" {
  value = module.compute.ecr_repository_urls
}

output "ecr_repository_names" {
  value = module.compute.ecr_repository_names
}

output "phoenix_url" {
  value = module.compute.phoenix_url
}

output "grafana_url" {
  value = module.observability.grafana_url
}

output "guardrail_id" {
  value = module.guardrails.guardrail_id
}
