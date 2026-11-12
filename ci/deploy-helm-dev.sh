#!/usr/bin/env bash
# deploy-helm-dev.sh - Install or upgrade the `a2c` Helm release on the oan_dev k3s node
# Called by Jenkins for develop branch deployments, after "Package chart"
#
# Runs on the deploy agent with the kubeconfig of the shared deployer ServiceAccount (oan-service-helm cluster/; Jenkins binds it as
# KUBECONFIG); the agent needs helm and kubectl, no AWS access: the node pulls from ECR with its own IAM role
# (kubelet ecr-credential-provider, see docs/operations/oan-dev-deploy.md). `helm upgrade --install` makes
# the first install and every upgrade. The chart generates the admin, secret-key and JWT Secrets once and keeps them
# across upgrades.
set -euo pipefail

# Required environment variables from Jenkins
: "${KUBECONFIG:?KUBECONFIG is required (credential oan-dev-kubeconfig)}"
: "${A2C_NAMESPACE:?A2C_NAMESPACE is required}"
: "${ECR_REPO:?ECR_REPO is required}"
: "${IMMUTABLE_TAG:?IMMUTABLE_TAG is required}"

CHART_TGZ="built-chart/oan-a2c-service-${IMMUTABLE_TAG}.tgz"
VALUES=helm/environments/dev/oan-a2c-service.yaml
NS="${A2C_NAMESPACE}"

test -f "${CHART_TGZ}" || { echo "${CHART_TGZ} is missing: run package_chart.py package first" >&2; exit 1; }

echo "=== Deploying to oan_dev: namespace ${NS} ==="
echo "=== Image: ${ECR_REPO}:${IMMUTABLE_TAG} ==="

# The postgres password the create-site job uses, copied from the frappe-common release.
kubectl -n "${NS}" create secret generic oan-frappe-common-db \
  --from-literal=password="$(kubectl -n frappe-common get secret oan-frappe-common-db -o jsonpath='{.data.password}' | base64 -d)" \
  --dry-run=client -o yaml | kubectl apply -f -

# --atomic only once a revision has deployed: a failed or cancelled upgrade then rolls back to it.
# Before that there is nothing to roll back to, and --atomic would uninstall the release, the
# a2c-frappe PVC with the site's files included.
ATOMIC=()
if helm -n "${NS}" history a2c -o json 2>/dev/null | grep -q '"status":"deployed"'; then
  ATOMIC=(--atomic)
fi
if ! helm upgrade --install a2c "${CHART_TGZ}" --namespace "${NS}" -f "${VALUES}" \
  --set frappe.image.tag="${IMMUTABLE_TAG}" \
  --set frappe.siteConfig.seed.image.tag="${IMMUTABLE_TAG}" \
  ${ATOMIC[@]+"${ATOMIC[@]}"} \
  --wait --wait-for-jobs --timeout 30m; then
  echo "=== helm upgrade failed ==="
  helm -n "${NS}" history a2c --max 3 || true
  kubectl -n "${NS}" get jobs,pods || true
  exit 1
fi

helm -n "${NS}" status a2c
kubectl -n "${NS}" get pods

echo "=== oan_dev deploy complete: a2c ${IMMUTABLE_TAG} ==="
