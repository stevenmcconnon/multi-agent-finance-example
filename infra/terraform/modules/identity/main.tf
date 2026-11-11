# Cognito user pool: authorization code + PKCE only (docs/CONTRACTS.md "Auth flow decision").
# The API keys the principal on the ID token's `cognito:username` (so usernames are the plain seed
# names: no `username_attributes = ["email"]` here) and reads the org from the one `cognito:groups`
# entry named `org:<slug>`. Both claims are in every Cognito ID token by default. Groups prefixed
# `role:` mirror the seeded roles; the database stays the authority (docs/CONTRACTS.md "JWT").
resource "aws_cognito_user_pool" "this" {
  name = var.project
  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }
  admin_create_user_config {
    allow_admin_create_user_only = true # demo users come from Terraform, nobody self-registers
  }
  auto_verified_attributes = ["email"]
  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true
  }
}

resource "random_string" "domain" {
  length  = 6
  upper   = false
  special = false
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = "${var.project}-${random_string.domain.result}"
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${var.project}-web"
  user_pool_id                         = aws_cognito_user_pool.this.id
  generate_secret                      = false # public SPA client: PKCE replaces the secret
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  callback_urls                        = var.callback_urls
  logout_urls                          = var.callback_urls
  supported_identity_providers         = ["COGNITO"]
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  access_token_validity                = 60
  id_token_validity                    = 60
  token_validity_units {
    access_token = "minutes"
    id_token     = "minutes"
  }
}

resource "aws_cognito_user_group" "this" {
  for_each     = toset(["org:acme", "finance", "analysts", "engineering", "role:analyst", "role:viewer", "role:admin"])
  name         = each.value
  user_pool_id = aws_cognito_user_pool.this.id
}

# Demo identities mirror db/migrations/0002_seed_dev.sql: alice (finance, analysts; analyst),
# bob (engineering; viewer), carol (admin), all in org `acme`.
locals {
  users = {
    alice = ["org:acme", "finance", "analysts", "role:analyst"]
    bob   = ["org:acme", "engineering", "role:viewer"]
    carol = ["org:acme", "role:admin"]
  }
  memberships = merge([for u, gs in local.users : { for g in gs : "${u}/${g}" => { user = u, group = g } }]...)
}

resource "aws_cognito_user" "demo" {
  for_each           = local.users
  user_pool_id       = aws_cognito_user_pool.this.id
  username           = each.key
  temporary_password = var.demo_passwords[each.key]
  attributes = {
    email          = "${each.key}@example.com"
    email_verified = "true"
  }
}

resource "aws_cognito_user_in_group" "demo" {
  for_each     = local.memberships
  user_pool_id = aws_cognito_user_pool.this.id
  username     = aws_cognito_user.demo[each.value.user].username
  group_name   = aws_cognito_user_group.this[each.value.group].name
}
