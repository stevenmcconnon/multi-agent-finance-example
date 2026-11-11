variable "project" {
  type = string
}

variable "region" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "alb_security_group_id" {
  type = string
}

variable "service_security_group_id" {
  type = string
}

variable "acm_certificate_arn" {
  type = string
}

variable "image_tag" {
  type = string
}

variable "db_secret_arn" {
  type = string
}

variable "db_address" {
  type = string
}

variable "db_username" {
  type = string
}

variable "redis_url" {
  type = string
}

variable "cognito_user_pool_id" {
  type = string
}

variable "cognito_client_id" {
  type = string
}

variable "avp_policy_store_id" {
  type = string
}

variable "avp_policy_store_arn" {
  type = string
}

variable "model_ids" {
  description = "Bedrock model ids; the task role may invoke only these four."
  type = object({
    generation = string
    verifier   = string
    embedding  = string
    rerank     = string
  })
}

variable "extra_api_containers" {
  description = "Sidecar container definitions appended to the api task (ADOT from modules/observability)."
  type        = list(any)
  default     = []
}

variable "web_origin" {
  description = "Browser origin of the UI (CloudFront URL), allowed by the API's CORS."
  type        = string
}
