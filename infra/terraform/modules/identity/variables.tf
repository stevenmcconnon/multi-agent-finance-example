variable "project" {
  type = string
}

variable "callback_urls" {
  type = list(string)
}

variable "demo_passwords" {
  type      = map(string)
  sensitive = true
  validation {
    condition     = alltrue([for u in ["alice", "bob", "carol"] : contains(keys(var.demo_passwords), u)])
    error_message = "demo_passwords needs keys alice, bob and carol."
  }
}
