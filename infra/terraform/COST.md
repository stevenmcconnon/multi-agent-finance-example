# What the dev environment costs

Sizes are the ones in `modules/*`. Prices are us-east-1 list prices, rounded; nothing here was
metered from a live run of this repo.

| Resource | Size | $/month |
|---|---|---|
| Amazon Relational Database Service (RDS) Postgres 16 | db.t4g.small, 20 GB gp3 storage, single availability zone (AZ) | ~25 |
| Fargate (serverless containers) | 4 tasks x 0.5 virtual CPU (vCPU) / 1 GB (api, temporal, phoenix, ingest-worker) | ~60 |
| Application Load Balancer | 1, low traffic | ~18 |
| ElastiCache Valkey | cache.t4g.micro, 1 node | ~12 |
| Bedrock | Sonnet 4.5 + Haiku 4.5 + Titan embed + Cohere rerank + Guardrails, demo traffic | 10-30 |
| CloudWatch logs/metrics, Virtual Private Cloud (VPC) interface endpoints (5 x ~$7), Secrets Manager, Elastic Container Registry (ECR), S3 storage with CloudFront | | ~15 + ~35 endpoints |
| EventBridge Scheduler, Cognito (under 50k monthly active users, MAU), Verified Permissions (demo volume) | | ~0 |
| **Total** | | **~$140-160 (closer to $175 with the five interface endpoints), about $5/day** |

Not included: Amazon Managed Grafana (`enable_grafana=true`, ~$9 per editor per month), a network address translation (NAT)
gateway (none: tasks sit in public subnets, see `modules/network`), an AWS Certificate Manager (ACM) certificate (free) and
the Route 53 zone for `api_domain` (~$0.50).

The Bedrock line is a rough demo-traffic allowance, not a measured or itemized forecast. Guardrails add
usage-based evaluation charges on model calls plus the explicit input/output checks; measure that line from
the deployed request mix before treating the total as a budget.

## Tear it down

```sh
make tf-destroy          # scripts/teardown.sh: empties the ECR repos, then `terraform destroy`
```

The RDS instance has `skip_final_snapshot = true` and the secret `recovery_window_in_days = 0`, so
a destroy leaves nothing billing. Re-ingesting the Apple (AAPL) and Microsoft (MSFT) filings after a rebuild is `make ingest`.

## User interface (UI) note

`modules/web` serves the Vite build: `cd apps/web && pnpm build && aws s3 sync dist s3://<web_bucket>`,
then `aws cloudfront create-invalidation`. The VITE_* values (the application programming interface (API) URL, Cognito domain and client id,
redirect uniform resource identifier (URI) = `web_url`) are baked at build time, so build after the first `apply` has printed them.
