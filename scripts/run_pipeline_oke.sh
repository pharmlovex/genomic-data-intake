#!/usr/bin/env bash
# Run variant calling for a READY_FOR_ANALYSIS batch on OKE.
#
#   ./scripts/run_pipeline_oke.sh <batch_id> s3://<bucket>/reference/reference.fa
#
# One-off setup (after `enable_oke = true` and an apply):
#   oci ce cluster create-kubeconfig --cluster-id $(tofu -chdir=infra output -raw oke_cluster_id) --file ~/.kube/config
#   kubectl apply -f pipeline/k8s/setup.yaml
#   kubectl -n genomics create secret generic oci-s3-credentials \
#     --from-literal=AWS_ACCESS_KEY_ID=<customer secret key id> --from-literal=AWS_SECRET_ACCESS_KEY=<secret>
set -euo pipefail
cd "$(dirname "$0")/.."
TF=${TF:-$(command -v terraform || command -v tofu)}

export BATCH_ID=${1:?batch id}
export REFERENCE=${2:?s3:// path to reference FASTA}
export BATCH_SHORT=${BATCH_ID:0:8}
export BUCKET=$($TF -chdir=infra output -raw bucket)
export OCI_NAMESPACE=$($TF -chdir=infra output -raw namespace)
export TOOLS_IMAGE=${TOOLS_IMAGE:-$($TF -chdir=infra output -raw ocir_prefix)/pipeline-tools:0.1.0}
export NXF_IMAGE=${NXF_IMAGE:-nextflow/nextflow:24.10.0}
export PIPELINE_REPO=${PIPELINE_REPO:-https://github.com/pharmlovex/genome-intake-oci}
export PIPELINE_REVISION=${PIPELINE_REVISION:-main}

envsubst < pipeline/k8s/run-job.yaml | kubectl apply -f -
echo "follow: kubectl -n genomics logs -f job/variant-calling-$BATCH_SHORT"
