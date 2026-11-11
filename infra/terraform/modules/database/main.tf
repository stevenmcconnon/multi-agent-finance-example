# RDS Postgres 16 with pgvector. The `vector` extension is created by db/migrations/0001_schema.sql
# (`CREATE EXTENSION IF NOT EXISTS vector`); RDS ships it in the pg16 engine, nothing to enable here.
resource "random_password" "master" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "this" {
  name       = var.project
  subnet_ids = var.subnet_ids
}

resource "aws_db_parameter_group" "this" {
  name   = "${var.project}-pg16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "this" {
  identifier             = var.project
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = "db.t4g.small"
  allocated_storage      = 20
  storage_type           = "gp3"
  db_name                = "harness"
  username               = "harness"
  password               = random_password.master.result
  port                   = 5432
  db_subnet_group_name   = aws_db_subnet_group.this.name
  parameter_group_name   = aws_db_parameter_group.this.name
  vpc_security_group_ids = [var.security_group_id]
  publicly_accessible    = false
  storage_encrypted      = true
  # In production: multi_az = true, deletion_protection = true, 7-35 day backups, a final snapshot.
  # In this demo: single AZ, no final snapshot and 1-day backups, because the data is public EDGAR
  #   filings re-ingestable in minutes and `terraform destroy` must finish without a manual step.
  multi_az                = false
  backup_retention_period = 1
  skip_final_snapshot     = true
  deletion_protection     = false
  apply_immediately       = true
}

# One secret, JSON, so ECS can inject either the whole DSN (`:url::`) or the password alone
# (`:password::`, Temporal's POSTGRES_PWD) from the same ARN.
resource "aws_secretsmanager_secret" "db" {
  name                    = "${var.project}/database"
  recovery_window_in_days = 0 # destroy must be able to recreate the name the same day
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = aws_db_instance.this.username
    password = random_password.master.result
    host     = aws_db_instance.this.address
    port     = aws_db_instance.this.port
    dbname   = aws_db_instance.this.db_name
    url      = "postgresql://${aws_db_instance.this.username}:${random_password.master.result}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}?sslmode=require"
  })
}
