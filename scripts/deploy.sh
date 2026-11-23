#!/usr/bin/env bash
# Two-pass deploy: base infrastructure, then images, then functions + API.
#
# Prerequisites: OCI CLI config (~/.oci/config), Terraform (or OpenTofu), Docker, and a
# Docker login to OCIR:  docker login <region>.ocir.io -u '<namespace>/<username>'
# (the password is an OCI "Auth Token" from your user profile).
#
#   ./scripts/deploy.sh            # infra + validator + functions
#   ./scripts/deploy.sh --pipeline # also build the pipeline tools image
set -euo pipefail
cd "$(dirname "$0")/.."

TF=${TF:-$(command -v terraform || command -v tofu)}
TAG=${TAG:-0.1.0}
TFVARS=infra/terraform.tfvars

[[ -f $TFVARS ]] || { echo "missing $TFVARS: copy infra/terraform.tfvars.example and fill it in" >&2; exit 1; }

# Set `name = "value"` in terraform.tfvars: replace an active line if there is one,
# otherwise append. Commented-out lines are left alone. Rewrites in place so the
# file keeps its permissions.
set_tfvar() { # name value
  local updated
  updated=$(awk -v name="$1" -v value="$2" '
    $0 ~ "^[[:space:]]*" name "[[:space:]]*=" { printf "%-23s = \"%s\"\n", name, value; done = 1; next }
    { print }
    END { if (!done) printf "%-23s = \"%s\"\n", name, value }
  ' "$TFVARS")
  printf '%s\n' "$updated" > "$TFVARS"
}

echo "==> 1/4 base infrastructure"
$TF -chdir=infra init -input=false >/dev/null
$TF -chdir=infra apply -input=false -auto-approve

PREFIX=$($TF -chdir=infra output -raw ocir_prefix)

build_push() { # name dockerfile context [build-arg]
  local image="$PREFIX/$1:$TAG"
  echo "    $image"
  docker build --platform linux/amd64 -q -f "$2" ${4:+--build-arg "$4"} -t "$image" "$3" >/dev/null
  docker push -q "$image" >/dev/null
}

echo "==> 2/4 validator image"
build_push validator validator/Dockerfile .

echo "==> 3/4 function images"
build_push intake-api functions/Dockerfile . FUNC=intake-api
build_push on-object-created functions/Dockerfile . FUNC=on-object-created

if [[ "${1:-}" == "--pipeline" ]]; then
  echo "    pipeline tools image (slow: conda solve)"
  build_push pipeline-tools pipeline/Dockerfile pipeline
fi

echo "==> 4/4 functions, events rule and API gateway"
# Recorded in terraform.tfvars (not passed as -var) so a later plain `terraform apply`
# keeps the functions and API instead of destroying them.
set_tfvar validator_image_tag "$TAG"
set_tfvar intake_api_image "$PREFIX/intake-api:$TAG"
set_tfvar on_object_created_image "$PREFIX/on-object-created:$TAG"
echo "    image names saved to $TFVARS"
$TF -chdir=infra apply -input=false -auto-approve

echo
echo "API: $($TF -chdir=infra output -raw api_url)"
echo "Next: confirm the subscription e-mail from OCI Notifications, then:"
echo "  python scripts/make_test_data.py --out testdata --with-problems"
echo "  INTAKE_API=<API above> INTAKE_API_KEY=<your api_key> python scripts/upload_batch.py testdata --samplesheet-last"
