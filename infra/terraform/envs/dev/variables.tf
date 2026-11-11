variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "multi-agent-finance-example"
}

variable "acm_certificate_arn" {
  description = "ACM certificate for the ALB HTTPS listener (issued in `region`)."
  type        = string
}

variable "api_domain" {
  description = "Hostname the certificate covers; the UI's VITE_API_URL is https://<api_domain>."
  type        = string
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "model_ids" {
  description = "Bedrock model ids (defaults match .env.example)."
  type = object({
    generation = string
    verifier   = string
    embedding  = string
    rerank     = string
  })
  default = {
    generation = "anthropic.claude-sonnet-4-5-20250929-v1:0"
    verifier   = "anthropic.claude-haiku-4-5-20251001-v1:0"
    embedding  = "amazon.titan-embed-text-v2:0"
    rerank     = "cohere.rerank-v3-5:0"
  }
}

variable "demo_password_alice" {
  type      = string
  sensitive = true
}

variable "demo_password_bob" {
  type      = string
  sensitive = true
}

variable "demo_password_carol" {
  type      = string
  sensitive = true
}

variable "enable_grafana" {
  type    = bool
  default = false
}
