variable "project" {
  type = string
}

variable "region" {
  type = string
}

variable "phoenix_endpoint" {
  description = "OTLP/http base URL of the Phoenix service inside the cluster."
  type        = string
}

variable "enable_grafana" {
  type    = bool
  default = false
}
