#!/usr/bin/env bash
# deploy-helm-dev.sh - Install or upgrade the `a2c` Helm release on the oan_dev k3s node
# Called by Jenkins for develop branch deployments, after "Package chart"
#
# Runs on the deploy agent with the kubeconfig of ci/cluster/oan-dev-deployer.yaml (Jenkins binds it as
# KUBECONFIG); the agent needs helm, kubectl and the aws cli. `helm upgrade --install` makes the first
# install and every upgrade. The chart generates the admin, secret-key and JWT Secrets once and keeps them
# across upgrades.
set -euo pipefail

# Required environment variables from Jenkins
: "${KUBECONFIG:?KUBECONFIG is required (credential oan-dev-kubeconfig)}"
: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID is required}"
: "${AWS_REGION:?AWS_REGION is required}"
: "${A2C_NAMESPACE:?A2C_NAMESPACE is required}"
: "${ECR_REPO:?ECR_REPO is required}"
: "${IMMUTABLE_TAG:?IMMUTABLE_TAG is required}"

REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
CHART_TGZ="built-chart/oan-a2c-service-${IMMUTABLE_TAG}.tgz"
VALUES=helm/environments/dev/oan-a2c-service.yaml
NS="${A2C_NAMESPACE}"

test -f "${CHART_TGZ}" || { echo "${CHART_TGZ} is missing: run package_chart.py package first" >&2; exit 1; }

echo "=== Deploying to oan_dev: namespace ${NS} ==="
echo "=== Image: ${ECR_REPO}:${IMMUTABLE_TAG} ==="

# ECR pull Secret. The token is valid 12 hours, so it is refreshed on every deploy to keep image and
# seed-job pulls working.
aws ecr get-login-password --region "${AWS_REGION}" | kubectl -n "${NS}" create secret docker-registry ecr-pull-secret \
  --docker-server="${REGISTRY}" --docker-username=AWS --docker-password="$(cat)" \
  --dry-run=client -o yaml | kubectl apply -f -

# The postgres password the create-site job uses, copied from the frappe-common release.
kubectl -n "${NS}" create secret generic oan-frappe-common-db \
  --from-literal=password="$(kubectl -n frappe-common get secret oan-frappe-common-db -o jsonpath='{.data.password}' | base64 -d)" \
  --dry-run=client -o yaml | kubectl apply -f -

# No --atomic: a failed first install would uninstall the release, generated Secrets included.
if ! helm upgrade --install a2c "${CHART_TGZ}" --namespace "${NS}" -f "${VALUES}" \
  --set frappe.image.tag="${IMMUTABLE_TAG}" \
  --wait --wait-for-jobs --timeout 30m; then
  echo "=== helm upgrade failed ==="
  helm -n "${NS}" history a2c --max 3 || true
  kubectl -n "${NS}" get jobs,pods || true
  exit 1
fi

helm -n "${NS}" status a2c
kubectl -n "${NS}" get pods

echo "=== oan_dev deploy complete: a2c ${IMMUTABLE_TAG} ==="
