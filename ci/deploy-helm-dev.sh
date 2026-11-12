#!/usr/bin/env bash
# deploy-helm-dev.sh - Install or upgrade the `a2c` Helm release on the oan_dev k3s node
# Called by Jenkins for develop branch deployments, after "Package chart"
#
# helm and kubectl run on the node over SSH (the node's ~/.kube/config); the agent only needs
# ssh, scp and the aws cli. `helm upgrade --install` makes the first install and every upgrade.
# The chart generates the admin, secret-key and JWT Secrets once and keeps them across upgrades.
set -euo pipefail

# Required environment variables from Jenkins
: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID is required}"
: "${AWS_REGION:?AWS_REGION is required}"
: "${SSH_KEY:?SSH_KEY is required}"
: "${SSH_USER:?SSH_USER is required}"
: "${OAN_DEV_IP:?OAN_DEV_IP is required}"
: "${A2C_NAMESPACE:?A2C_NAMESPACE is required}"
: "${BUILD_NUMBER:?BUILD_NUMBER is required}"
: "${ECR_REPO:?ECR_REPO is required}"
: "${IMMUTABLE_TAG:?IMMUTABLE_TAG is required}"

REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
CHART_TGZ="built-chart/oan-a2c-service-${IMMUTABLE_TAG}.tgz"
VALUES=helm/environments/dev/oan-a2c-service.yaml
REMOTE="${SSH_USER}@${OAN_DEV_IP}"
SSH_OPTS=(-i "${SSH_KEY}" -o StrictHostKeyChecking=no)
WORK="/tmp/a2c-deploy-${BUILD_NUMBER}"

test -f "${CHART_TGZ}" || { echo "${CHART_TGZ} is missing: run package_chart.py package first" >&2; exit 1; }

echo "=== Deploying to oan_dev: ${OAN_DEV_IP}, namespace ${A2C_NAMESPACE} ==="
echo "=== Image: ${ECR_REPO}:${IMMUTABLE_TAG} ==="

ssh "${SSH_OPTS[@]}" "${REMOTE}" "mkdir -p ${WORK}"
scp "${SSH_OPTS[@]}" "${CHART_TGZ}" "${VALUES}" "${REMOTE}:${WORK}/"

# Namespace and ECR pull Secret. The node has no aws cli, so the token (valid 12 hours) is made
# here and sent on stdin; refreshing it on every deploy keeps image and seed-job pulls working.
aws ecr get-login-password --region "${AWS_REGION}" | ssh "${SSH_OPTS[@]}" "${REMOTE}" "set -euo pipefail
  kubectl create namespace ${A2C_NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n ${A2C_NAMESPACE} create secret docker-registry ecr-pull-secret \
    --docker-server=${REGISTRY} --docker-username=AWS --docker-password=\"\$(cat)\" \
    --dry-run=client -o yaml | kubectl apply -f -"

# Quoted heredoc: nothing below expands on the agent; the values come in as variables.
ssh "${SSH_OPTS[@]}" "${REMOTE}" \
  "NS='${A2C_NAMESPACE}' TAG='${IMMUTABLE_TAG}' WORK='${WORK}' CHART='${WORK}/$(basename "${CHART_TGZ}")' VALUES='${WORK}/$(basename "${VALUES}")' bash -s" << 'SSHEOF'
set -euo pipefail
trap 'rm -rf "$WORK"' EXIT

# The postgres password the create-site job uses, copied from the frappe-common release.
kubectl -n "$NS" create secret generic oan-frappe-common-db \
  --from-literal=password="$(kubectl -n frappe-common get secret oan-frappe-common-db -o jsonpath='{.data.password}' | base64 -d)" \
  --dry-run=client -o yaml | kubectl apply -f -

# No --atomic: a failed first install would uninstall the release, generated Secrets included.
if ! helm upgrade --install a2c "$CHART" --namespace "$NS" -f "$VALUES" \
  --set frappe.image.tag="$TAG" \
  --wait --wait-for-jobs --timeout 30m; then
  echo "=== helm upgrade failed ==="
  helm -n "$NS" history a2c --max 3 || true
  kubectl -n "$NS" get jobs,pods || true
  exit 1
fi

helm -n "$NS" status a2c
kubectl -n "$NS" get pods
SSHEOF

echo "=== oan_dev deploy complete: a2c ${IMMUTABLE_TAG} ==="
