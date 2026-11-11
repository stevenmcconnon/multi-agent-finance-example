output "address" {
  value = aws_db_instance.this.address
}

output "username" {
  value = aws_db_instance.this.username
}

output "secret_arn" {
  description = "JSON secret with keys username, password, host, port, dbname, url."
  value       = aws_secretsmanager_secret.db.arn
}
