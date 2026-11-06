#!/usr/bin/env bash
# Deploy one client stack to AWS. Needs only Docker: terraform and the AWS CLI run from their official images.
#
#   AWS credentials: ~/.aws (profile via AWS_PROFILE) or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN
#   ./scripts/deploy.sh demo            # plan + apply clients/demo.tfvars
#   ./scripts/deploy.sh demo plan       # plan only
#   ./scripts/deploy.sh demo destroy    # tear everything down
set -euo pipefail
CLIENT="${1:?usage: deploy.sh <client> [apply|plan|destroy]}"
ACTION="${2:-apply}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Git Bash on Windows: Docker needs C:/... paths, not /c/...
command -v cygpath >/dev/null && ROOT="$(cygpath -m "$ROOT")"
TF_DIR="$ROOT/infra/terraform"
VARS="clients/${CLIENT}.tfvars"
TAG="${IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || date +%s)}"
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: don't rewrite /w paths

AWS_ENV=(-e AWS_PROFILE -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN -e AWS_REGION
         -e AWS_DEFAULT_REGION -e TF_VAR_anthropic_api_key -v "$HOME/.aws:/root/.aws:ro")
# the repo root is mounted (not just infra/terraform) because the Lambda zips are built from serverless/
tf()  { docker run --rm -i "${AWS_ENV[@]}" -v "$ROOT:/repo" -w /repo/infra/terraform hashicorp/terraform:1.17.1 "$@"; }
aws() { docker run --rm -i "${AWS_ENV[@]}" amazon/aws-cli:2.27.50 "$@"; }

[ -f "$TF_DIR/$VARS" ] || { echo "missing $TF_DIR/$VARS"; exit 1; }
echo "==> identity"; aws sts get-caller-identity --output table
tf init -input=false -upgrade >/dev/null

if [ "$ACTION" = "destroy" ]; then
  tf destroy -input=false -var-file="$VARS" -var "image_tag=$TAG"
  exit 0
fi
if [ "$ACTION" = "plan" ]; then
  tf plan -input=false -var-file="$VARS" -var "image_tag=$TAG"
  exit 0
fi

echo "==> 1/4 registries"
tf apply -input=false -auto-approve -var-file="$VARS" -var "image_tag=$TAG" -target=aws_ecr_repository.repo >/dev/null
ECR_JSON="$(tf output -json ecr)"
REGION="$(grep -E '^region' "$TF_DIR/$VARS" | sed -E 's/.*"(.*)".*/\1/')"
REGISTRY="$(echo "$ECR_JSON" | python -c 'import sys,json;print(next(iter(json.load(sys.stdin).values())).split("/")[0])')"
repo() { echo "$ECR_JSON" | python -c "import sys,json;print(json.load(sys.stdin)['$1'])"; }

echo "==> 2/4 build + push images ($TAG)"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
NS="${CLIENT}.portalpilot.local"
docker build --platform linux/amd64 -t "$(repo backend):$TAG" "$ROOT/backend"
docker build --platform linux/amd64 -t "$(repo portal):$TAG" "$ROOT/mock_portal"
docker build --platform linux/amd64 --build-arg "API_UPSTREAM=api.$NS:8000" -t "$(repo web):$TAG" "$ROOT/frontend"
for r in backend portal web; do docker push "$(repo $r):$TAG"; done

echo "==> 3/4 infrastructure"
tf apply -input=false -auto-approve -var-file="$VARS" -var "image_tag=$TAG"

echo "==> 4/4 waiting for the console"
URL="$(tf output -raw console_url)"
for i in $(seq 1 90); do
  if curl -sf "$URL/api/healthz" >/dev/null; then echo "up: $URL"; break; fi
  sleep 10
done
SECRET="$(tf output -raw operator_token_secret)"
echo
echo "console:      $URL"
echo "mock portal:  $(tf output -raw mock_portal_url 2>/dev/null || true)"
echo "operator token:"
echo "  ./scripts/aws.sh secretsmanager get-secret-value --secret-id $SECRET --query SecretString --output text"
echo "e2e against AWS:"
echo "  python e2e/run_e2e.py --api $URL --portal $(tf output -raw mock_portal_url 2>/dev/null) --token <operator token> --chaos"
