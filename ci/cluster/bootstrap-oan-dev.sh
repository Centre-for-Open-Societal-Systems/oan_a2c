#!/usr/bin/env bash
# bootstrap-oan-dev.sh - Create the Jenkins deploy identity on the oan_dev k3s node and write its kubeconfig
# Run once on a machine that has admin kubectl access to the cluster (the node itself), then upload the file as
# the Jenkins "Secret file" credential `oan-dev-kubeconfig` (docs/operations/oan-dev-deploy.md).
# Re-running is safe and refreshes the kubeconfig. Never commit the output.
#
# Usage: bootstrap-oan-dev.sh <output-kubeconfig> [api-server-url]
#   api-server-url: an address the Jenkins agent can reach, default https://10.0.1.131:6443
set -euo pipefail

OUT="${1:?usage: bootstrap-oan-dev.sh <output-kubeconfig> [api-server-url]}"
SERVER="${2:-https://10.0.1.131:6443}"
NS=a2c
SECRET=jenkins-a2c-deployer-token
HERE="$(cd "$(dirname "$0")" && pwd)"

kubectl apply -f "${HERE}/oan-dev-deployer.yaml"

# The token controller fills the Secret a moment after it is created.
for _ in $(seq 1 30); do
  TOKEN="$(kubectl -n "${NS}" get secret "${SECRET}" -o jsonpath='{.data.token}' | base64 -d)"
  [ -n "${TOKEN}" ] && break
  sleep 1
done
[ -n "${TOKEN}" ] || { echo "token of ${SECRET} was not issued" >&2; exit 1; }

CA="$(mktemp)"
trap 'rm -f "${CA}"' EXIT
kubectl -n "${NS}" get secret "${SECRET}" -o jsonpath='{.data.ca\.crt}' | base64 -d > "${CA}"

umask 077
rm -f "${OUT}"
kubectl config --kubeconfig="${OUT}" set-cluster oan-dev --server="${SERVER}" \
  --certificate-authority="${CA}" --embed-certs=true >/dev/null
kubectl config --kubeconfig="${OUT}" set-credentials jenkins --token="${TOKEN}" >/dev/null
kubectl config --kubeconfig="${OUT}" set-context oan-dev --cluster=oan-dev --user=jenkins --namespace="${NS}" >/dev/null
kubectl config --kubeconfig="${OUT}" use-context oan-dev >/dev/null

echo "kubeconfig written to ${OUT} (server ${SERVER})."
echo "Upload it as the Jenkins Secret file credential 'oan-dev-kubeconfig', then delete it."
