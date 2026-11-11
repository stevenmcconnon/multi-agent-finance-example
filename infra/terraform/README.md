# infra/terraform/

The L1 Cloud layer as code: one `dev` environment built from nine small modules. Terraform is a tool that creates cloud resources from text files.

## Files

| Path | What it owns |
|---|---|
| `COST.md` | Monthly cost estimate and the teardown steps. Read it before `apply`. |
| `envs/dev/` | The one environment: `main.tf` wires modules, plus `variables.tf`, `outputs.tf`, `versions.tf`, `backend.tf`, `.terraform.lock.hcl`. |
| `modules/network` | Virtual Private Cloud (VPC), two public subnets, security groups. |
| `modules/database` | RDS Postgres 16 with pgvector; random master password. |
| `modules/cache` | One-node ElastiCache Valkey (group cache and tool results). |
| `modules/identity` | Cognito user pool: authorization code with PKCE only; `role:*` and `org:<slug>` groups. |
| `modules/policy` | Verified Permissions policy store holding the Cedar policies. |
| `modules/observability` | ADOT sidecar definition: metrics to CloudWatch, traces to Phoenix. |
| `modules/compute` | ECS cluster, Fargate services (api, temporal, phoenix, ingest-worker), load balancer, Elastic Container Registry (ECR). |
| `modules/schedule` | EventBridge Scheduler: daily EDGAR refresh using the ingest CLI. |
| `modules/web` | S3 bucket and CloudFront for the web build. |

Each module has `main.tf`, `variables.tf` and `outputs.tf`.

## Validate, plan, destroy

```sh
make tf-validate   # terraform fmt -check, init -backend=false, validate; no AWS account needed
make tf-plan       # init + plan in envs/dev; needs AWS credentials
make tf-destroy    # scripts/teardown.sh: empties ECR repos, then terraform destroy
```

`apply` is run by hand: `cd envs/dev && terraform apply`. Cost and what the destroy removes: [`COST.md`](COST.md).

## Rules that hold here

- `terraform fmt -check -recursive` and `validate` must pass (`make tf-validate`).
- The policy module uploads the same Cedar text the API evaluates locally.
- Identity: no implicit flow, no client secret (ADR 0007).

## Production vs this demo

In production: remote state in S3 with locking, private subnets with network address translation (NAT), multi-AZ database with deletion protection and backups, Temporal Cloud, CloudFront with a custom domain and WAF.
In this demo: local state, public subnets with no NAT, a single-AZ database that skips its final snapshot, Temporal on Fargate, and the default CloudFront certificate, because each production choice adds recurring cost.

## Read next

- [`COST.md`](COST.md).
- [`../../docs/diagrams/aws.svg`](../../docs/diagrams/aws.svg).
