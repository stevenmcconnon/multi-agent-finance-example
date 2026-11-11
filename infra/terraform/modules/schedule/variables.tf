variable "project" {
  type = string
}

variable "cluster_arn" {
  type = string
}

variable "task_definition_arn" {
  type = string
}

variable "container_name" {
  type = string
}

variable "pass_role_arns" {
  description = "Task + execution role ARNs the scheduler may pass to RunTask."
  type        = list(string)
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_id" {
  type = string
}
