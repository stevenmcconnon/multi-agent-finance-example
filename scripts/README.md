# scripts/

Operator scripts that are not part of the running system.

## Files

| File | What it owns |
|---|---|
| `teardown.sh` | Destroys the dev environment: asks you to type `destroy`, empties every ECR (Elastic Container Registry) repository (a non-empty repo blocks `terraform destroy`), then runs `terraform destroy` in `infra/terraform/envs/dev`. |

## How it is used

`make tf-destroy` runs it. It needs AWS credentials, `terraform`, the AWS command line interface and `python3`.

## Rules that hold here

- It aborts unless you type `destroy` exactly.
- It leaves nothing billing: the database skips its final snapshot ([`../infra/terraform/COST.md`](../infra/terraform/COST.md)).
- A repeatable operator task becomes a named script here or a `make` target, not a pasted command.

## Read next

- [`../infra/terraform/README.md`](../infra/terraform/README.md).
