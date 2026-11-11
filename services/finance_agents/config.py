import re

from pydantic import field_validator
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
    # Inference profile ids: the bare model ids cannot be invoked on demand.
    bedrock_generation_model: str = "us.anthropic.claude-sonnet-5-5"
    bedrock_verifier_model: str = "us.anthropic.claude-haiku-5-5"
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

    bedrock_guardrail_id: str = ""  # empty = Bedrock Guardrails off (local demo)
    bedrock_guardrail_version: str = "DRAFT"

    @field_validator("bedrock_guardrail_id")
    @classmethod
    def _guardrail_id_is_an_id(cls, v: str) -> str:
        """Refuse to start on anything that is not a guardrail id or ARN. An env file line such as
        `BEDROCK_GUARDRAIL_ID=   # comment` hands the comment over as the value, which once switched the
        content filter on with a nonsense id and failed every question."""
        v = v.strip()
        if v and not re.fullmatch(r"[a-z0-9]+|arn:aws[a-z-]*:bedrock:[a-z0-9-]+:\d{12}:guardrail/[a-z0-9]+", v):
            raise ValueError(f"BEDROCK_GUARDRAIL_ID {v!r} is not a guardrail id; leave it empty to turn it off")
        return v

    hr_rate_limit_per_minute: int = 30


settings = Settings()
