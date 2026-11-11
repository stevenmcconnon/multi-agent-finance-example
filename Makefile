# Local loop: make up → make ingest → open http://localhost:3000
TF_DEV := infra/terraform/envs/dev
CLI    := docker compose exec api python -m finance_agents.ingest.cli

.PHONY: up down logs migrate seed ingest test lint web tf-validate tf-plan tf-destroy

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

migrate:          ## applies db/migrations in order (0002 is the dev seed)
	$(CLI) migrate

seed: migrate

ingest:
	$(CLI) run --tickers AAPL,MSFT --forms 10-K,8-K --limit 2

test:
	uv run pytest

lint:
	uv run ruff format --check services tests evals && uv run ruff check . && uv run mypy services/finance_agents
	cd apps/web && pnpm lint

web:
	cd apps/web && pnpm dev

tf-validate:
	terraform fmt -check -recursive infra/terraform
	cd $(TF_DEV) && terraform init -backend=false -input=false && terraform validate

tf-plan:
	cd $(TF_DEV) && terraform init -input=false && terraform plan

tf-destroy:
	scripts/teardown.sh
