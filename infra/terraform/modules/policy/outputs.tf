output "policy_store_id" {
  value = aws_verifiedpermissions_policy_store.this.id
}

output "policy_store_arn" {
  value = aws_verifiedpermissions_policy_store.this.arn
}

output "policy_ids" {
  description = "The @id names found in db/policies/*.cedar, one AVP policy each."
  value       = keys(aws_verifiedpermissions_policy.static)
}
