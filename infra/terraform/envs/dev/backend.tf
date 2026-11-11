# In production: remote state in S3 with DynamoDB locking (or Terraform Cloud), one key per env.
# In this demo: local state, because a $0 clone must `init` without any pre-existing AWS resource;
#   switch by uncommenting the block and running `terraform init -migrate-state`.
# terraform {
#   backend "s3" {
#     bucket         = "<state-bucket>"
#     key            = "harness-rag/dev.tfstate"
#     region         = "us-east-1"
#     dynamodb_table = "<lock-table>"
#     encrypt        = true
#   }
# }
