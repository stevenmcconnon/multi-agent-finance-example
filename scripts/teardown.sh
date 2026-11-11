#!/usr/bin/env bash
# Tears the dev environment down: ECR images first (a non-empty repo blocks `destroy`), then terraform.
set -euo pipefail
cd "$(dirname "$0")/../infra/terraform/envs/dev"

read -r -p "Destroy multi-agent-finance-example dev (RDS, ECS, Cognito, AVP, S3, ECR) in $(terraform output -raw region 2>/dev/null || echo '?')? type 'destroy': " answer
[[ "$answer" == "destroy" ]] || { echo "aborted"; exit 1; }

for repo in $(terraform output -json ecr_repository_names 2>/dev/null | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin)))'); do
  ids=$(aws ecr list-images --repository-name "$repo" --query 'imageIds[*]' --output json)
  [[ "$ids" == "[]" ]] || aws ecr batch-delete-image --repository-name "$repo" --image-ids "$ids" >/dev/null
  echo "emptied ECR $repo"
done

terraform destroy
