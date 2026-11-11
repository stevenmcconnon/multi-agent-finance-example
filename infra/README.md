# infra/

The L1 Cloud layer: everything that runs the system on Amazon Web Services (AWS), and the telemetry configuration.

## Files

| Path | What it owns |
|---|---|
| `terraform/` | Terraform modules and the `dev` environment, including the managed Bedrock Guardrail. See its [README](terraform/README.md). |
| `otel/collector.yaml` | OpenTelemetry collector config for local compose: receives traces, exports to Phoenix. |
| `grafana/api-dashboard.json` | A Grafana dashboard for the API metrics. |

## How it is used

`make tf-validate`, `make tf-plan` and `make tf-destroy` drive Terraform. `docker-compose.yml` mounts the collector config.

## Rules that hold here

- `terraform apply` stays a human command; the deploy workflow only plans. The Terraform passes `fmt` and `validate` in continuous integration (CI) and has not been applied to an AWS account.
- Never claim a cost that was not priced: [`terraform/COST.md`](terraform/COST.md) says its figures are list prices, not metered.
- Keep the Bedrock Guardrail module and [`../docs/GUARDRAILS.md`](../docs/GUARDRAILS.md) aligned.

## Production vs this demo

In production: Grafana (managed or self-hosted) with CloudWatch and Prometheus data sources; metrics reach CloudWatch through the ADOT (AWS Distro for OpenTelemetry) sidecar.
In this demo: the dashboard is a JSON file you import, because Grafana is optional (`enable_grafana`).

## Read next

- [`terraform/README.md`](terraform/README.md).
- [`../docs/diagrams/aws.svg`](../docs/diagrams/aws.svg).
