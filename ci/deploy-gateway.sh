#!/usr/bin/env bash
# deploy-gateway.sh - Install or upgrade the A2C APISIX routes (chart apisix/k8s) on the oan_dev k3s node
# Called by Jenkins for develop, after the app deploy, when the spec, the routes or the gateway values changed.
#
# The routes are generated from openapi/openapi_v1.public.yaml (apisix/generate_apisix_config_from_spec.py)
# and committed, so this installs what is in git. Runs on the deploy agent with the kubeconfig of
# the shared deployer ServiceAccount of oan-service-helm cluster/ (Jenkins binds it as KUBECONFIG); the agent needs helm and kubectl.
# APISIX and its Ingress Controller are a separate release (namespace GATEWAY_APISIX_NAMESPACE); this chart
# only holds ApisixRoute/ApisixUpstream objects, which the controller picks up.
# Manage the gateway with this chart or with `adc sync`, never both (apisix/README.md).
#
# Optional overrides: GATEWAY_RELEASE, GATEWAY_APISIX_NAMESPACE, GATEWAY_CHART, GATEWAY_VALUES.
set -euo pipefail

: "${KUBECONFIG:?KUBECONFIG is required (credential oan-dev-kubeconfig)}"
: "${A2C_NAMESPACE:?A2C_NAMESPACE is required}"

RELEASE="${GATEWAY_RELEASE:-a2c-gateway}"
APISIX_NS="${GATEWAY_APISIX_NAMESPACE:-api6-gateway}"
CHART="${GATEWAY_CHART:-apisix/k8s}"
VALUES="${GATEWAY_VALUES:-helm/environments/dev/oan-a2c-gateway.yaml}"
NS="${A2C_NAMESPACE}"

# The committed routes must match the spec. Needs oan_auth_service in this python; skipped otherwise.
if python3 -c 'import oan_auth_service' 2>/dev/null; then
  python3 apisix/generate_apisix_config_from_spec.py
  git diff --exit-code -- apisix/ \
    || { echo "apisix/ is out of date with the OpenAPI spec: run the generator and commit" >&2; exit 1; }
else
  echo "=== oan_auth_service not importable: skipping the generator drift check ==="
fi

echo "=== Gateway ${RELEASE}: namespace ${NS}, APISIX in ${APISIX_NS} ==="

# The rate limiter's Redis ships with the APISIX release, whose name carries a suffix that changes on
# every redeploy, so find the Service by its labels.
REDIS_SVC="$(kubectl -n "${APISIX_NS}" get svc \
  -l app.kubernetes.io/name=redis,app.kubernetes.io/component=master -o jsonpath='{.items[*].metadata.name}')"
case "${REDIS_SVC}" in
  ''|*' '*) echo "expected exactly one Redis master Service in ${APISIX_NS}, found: '${REDIS_SVC}'" >&2; exit 1 ;;
esac
REDIS_HOST="${REDIS_SVC}.${APISIX_NS}.svc.cluster.local"

helm lint "${CHART}" -f "${VALUES}" --set redis.host="${REDIS_HOST}"

if ! helm upgrade --install "${RELEASE}" "${CHART}" --namespace "${NS}" -f "${VALUES}" \
  --set redis.host="${REDIS_HOST}" --wait --timeout 5m; then
  echo "=== helm upgrade failed ==="
  helm -n "${NS}" history "${RELEASE}" --max 3 || true
  exit 1
fi

helm -n "${NS}" status "${RELEASE}"
kubectl -n "${NS}" get apisixroute,apisixupstream

echo "=== gateway deploy complete: ${RELEASE} ==="
