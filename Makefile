# Local loop: make up → make ingest → open http://localhost:3000
TF_DEV  := infra/terraform/envs/dev
CLI     := docker compose exec api python -m finance_agents.ingest.cli
API_URL := http://localhost:$(or $(API_PORT),8000)
# pytest, ruff and mypy live in the `dev` extra
UV      := uv run --extra dev

.PHONY: up up-bedrock down logs migrate seed ingest ingest-rebuild ingest-latest warm test lint web tf-validate tf-plan tf-destroy

up:
	docker compose up -d --build

# In production: Claude on Amazon Bedrock with Bedrock embeddings, reranking and guardrails (infra/terraform).
# In this demo: `make up` answers from an offline stub; `make up-bedrock` swaps in the real Claude models and
#   keeps everything else local (embeddings and reranking stay fastembed, because the index was built with it).
# This mode calls AWS and costs money per question. Credentials come from the host: AWS_PROFILE (~/.aws is
# mounted read-only) or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN; AWS_REGION and
# BEDROCK_GENERATION_MODEL / BEDROCK_VERIFIER_MODEL pass through when set.
up-bedrock:
	HR_MODEL_BACKEND=bedrock docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

migrate:          ## applies db/migrations in order (0002 is the dev seed)
	$(CLI) migrate

seed: migrate

ingest:           ## the five pinned demo filings (ingest/demo_corpus.py); fails unless all five are ready
	$(CLI) demo
	@$(MAKE) --no-print-directory warm

ingest-rebuild:   ## the same five, parsed and embedded again (after a parser or embedding change)
	$(CLI) demo --rebuild
	@$(MAKE) --no-print-directory warm

ingest-latest:    ## the newest 10-K and 8-K per ticker, whatever they are this week
	$(CLI) run --tickers AAPL,MSFT --forms 10-K,8-K --limit 1

warm:             ## one real question as alice, so the embedding and reranking models are loaded before a demo
	@start=$$(date +%s); \
	token=$$(curl -fsS -X POST $(API_URL)/auth/dev-token -H 'content-type: application/json' -d '{"user":"alice"}' \
	  | sed 's/.*"token":"\([^"]*\)".*/\1/') && \
	curl -fsS -N -o /dev/null -X POST $(API_URL)/chat -H "authorization: Bearer $$token" \
	  -H 'content-type: application/json' -d '{"question":"What risks does Apple describe about its supply chain?"}' && \
	echo "warm: first answer took $$(( $$(date +%s) - start ))s; the models are loaded"

test:             ## works with the stack down: database-gated tests skip
	$(UV) pytest

lint:
	$(UV) ruff format --check services tests evals && $(UV) ruff check . && $(UV) mypy services/finance_agents
	cd apps/web && pnpm install --frozen-lockfile --silent && pnpm lint

web:
	cd apps/web && pnpm dev

tf-validate:
	terraform fmt -check -recursive infra/terraform
	cd $(TF_DEV) && terraform init -backend=false -input=false && terraform validate

tf-plan:
	cd $(TF_DEV) && terraform init -input=false && terraform plan

tf-destroy:
	scripts/teardown.sh
