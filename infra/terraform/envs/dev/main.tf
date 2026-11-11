module "network" {
  source  = "../../modules/network"
  project = var.project
  region  = var.region
}

module "database" {
  source            = "../../modules/database"
  project           = var.project
  subnet_ids        = module.network.public_subnet_ids
  security_group_id = module.network.database_security_group_id
}

module "cache" {
  source            = "../../modules/cache"
  project           = var.project
  subnet_ids        = module.network.public_subnet_ids
  security_group_id = module.network.cache_security_group_id
}

module "web" {
  source  = "../../modules/web"
  project = var.project
}

module "identity" {
  source        = "../../modules/identity"
  project       = var.project
  callback_urls = ["${module.web.url}/auth/callback", "http://localhost:3000/auth/callback"]
  demo_passwords = {
    alice = var.demo_password_alice
    bob   = var.demo_password_bob
    carol = var.demo_password_carol
  }
}

module "policy" {
  source       = "../../modules/policy"
  project      = var.project
  policies_dir = "${path.root}/../../../../db/policies"
}

module "guardrails" {
  source  = "../../modules/guardrails"
  project = var.project
}

module "observability" {
  source           = "../../modules/observability"
  project          = var.project
  region           = var.region
  phoenix_endpoint = "http://phoenix:6006"
  enable_grafana   = var.enable_grafana
}

module "compute" {
  source                    = "../../modules/compute"
  project                   = var.project
  region                    = var.region
  vpc_id                    = module.network.vpc_id
  subnet_ids                = module.network.public_subnet_ids
  alb_security_group_id     = module.network.alb_security_group_id
  service_security_group_id = module.network.service_security_group_id
  acm_certificate_arn       = var.acm_certificate_arn
  image_tag                 = var.image_tag
  db_secret_arn             = module.database.secret_arn
  db_address                = module.database.address
  db_username               = module.database.username
  redis_url                 = module.cache.redis_url
  cognito_user_pool_id      = module.identity.user_pool_id
  cognito_client_id         = module.identity.client_id
  avp_policy_store_id       = module.policy.policy_store_id
  avp_policy_store_arn      = module.policy.policy_store_arn
  model_ids                 = var.model_ids
  bedrock_guardrail_id      = module.guardrails.guardrail_id
  bedrock_guardrail_version = module.guardrails.guardrail_version
  bedrock_guardrail_arn     = module.guardrails.guardrail_arn
  web_origin                = module.web.url
  extra_api_containers      = [module.observability.adot_container]
}

module "schedule" {
  source              = "../../modules/schedule"
  project             = var.project
  cluster_arn         = module.compute.cluster_arn
  task_definition_arn = module.compute.api_task_definition_arn
  container_name      = module.compute.api_container_name
  pass_role_arns      = [module.compute.task_role_arn, module.compute.execution_role_arn]
  subnet_ids          = module.network.public_subnet_ids
  security_group_id   = module.network.service_security_group_id
}
