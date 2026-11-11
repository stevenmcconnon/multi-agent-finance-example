from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    hr_mode: str = "local"
    hr_log_level: str = "INFO"
    hr_cors_origins: str = "http://localhost:3000"  # comma-separated
    database_url: str = "postgresql://harness:harness@localhost:5432/harness"
    redis_url: str = "redis://localhost:6379/0"

    hr_jwt_mode: str = "local"
    hr_jwt_secret: str = "dev-only-change-me"
    cognito_user_pool_id: str = ""
    cognito_region: str = "us-east-1"
    cognito_app_client_id: str = ""

    hr_authz_backend: str = "cedar_local"
    avp_policy_store_id: str = ""

    hr_model_backend: str = "fake"
    aws_region: str = "us-east-1"
    bedrock_generation_model: str = "anthropic.claude-sonnet-4-5-20250929-v1:0"
    bedrock_verifier_model: str = "anthropic.claude-haiku-4-5-20251001-v1:0"
    bedrock_embedding_model: str = "amazon.titan-embed-text-v2:0"
    bedrock_rerank_model: str = "cohere.rerank-v3-5:0"
    hr_embed_backend: str = "fastembed"
    hr_rerank_backend: str = "fastembed"

    edgar_user_agent: str = "multi-agent-finance-example contact@example.com"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    s3_raw_bucket: str = ""

    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_service_name: str = "multi-agent-finance-api"
    phoenix_collector_endpoint: str = "http://localhost:6006"

    hr_turn_token_budget: int = 40000
    hr_rerank_min_score: float | None = None  # overrides the reranker's own floor (its score scale differs per backend)
    hr_turn_tool_call_budget: int = 8
    hr_turn_timeout_seconds: int = 60


settings = Settings()
