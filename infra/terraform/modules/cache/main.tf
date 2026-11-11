# Single-node Valkey. The app reads REDIS_URL and treats it as a cache (group cache, tool results);
# losing it costs latency, never correctness, so one node with no replica is enough here.
resource "aws_elasticache_subnet_group" "this" {
  name       = var.project
  subnet_ids = var.subnet_ids
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id       = var.project
  description                = "${var.project} valkey"
  engine                     = "valkey"
  engine_version             = "8.0"
  node_type                  = "cache.t4g.micro"
  num_cache_clusters         = 1
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.this.name
  security_group_ids         = [var.security_group_id]
  automatic_failover_enabled = false
  at_rest_encryption_enabled = true
  transit_encryption_enabled = false # in-VPC only; the redis client URL stays redis:// not rediss://
  apply_immediately          = true
}
