#!/usr/bin/env bash
# =============================================================================
#  update-kustomize.sh — GitOps write-back (called by Jenkins on the staging branch).
#  Points an oan-kustomize overlay at a new image, commits, and pushes to main.
#  ArgoCD on node 41 then syncs apps/*/overlays/staging.
#
#  Usage: ci/update-kustomize.sh <overlay> <kustomize-image-name> <new-image-ref>
#     <overlay>              overlay dir under apps/oan-a2c/overlays/   (e.g. staging)
#     <kustomize-image-name> the image 'name' key in the base deployment (match key)
#     <new-image-ref>        full ECR URI:tag to deploy
#
#  Auth: MUST run inside withCredentials(gitUsernamePassword(credentialsId:'oan-deployer')).
#        The git-client plugin injects a short-lived installation token (contents:write
#        on oan-kustomize only) over HTTPS — no token in the URL or logs.
#  Agent prereqs: git, kustomize.
# =============================================================================
set -euo pipefail

OVERLAY="${1:?usage: update-kustomize.sh <overlay> <image-name> <image-ref>}"
IMG_NAME="${2:?missing kustomize image match-name}"
NEW_REF="${3:?missing new image ref}"

REPO_URL="https://github.com/Centre-for-Open-Societal-Systems/oan-kustomize.git"
OVERLAY_PATH="apps/oan-a2c/overlays/${OVERLAY}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo ">> clone oan-kustomize (main)"
git clone --depth 1 --branch main "$REPO_URL" "$WORK/repo"

echo ">> kustomize set image ${IMG_NAME}=${NEW_REF}  (${OVERLAY_PATH})"
( cd "$WORK/repo/${OVERLAY_PATH}" && kustomize edit set image "${IMG_NAME}=${NEW_REF}" )

cd "$WORK/repo"
git config user.name  "oan-deployer[bot]"
git config user.email "oan-deployer[bot]@users.noreply.github.com"
git add -A
if git diff --cached --quiet; then
  echo ">> no change (${NEW_REF} already current) — nothing to push"
  exit 0
fi
git commit -m "ci(oan-a2c): ${OVERLAY} image -> ${NEW_REF##*/}"
git push origin main
echo ">> pushed: ${OVERLAY} now on ${NEW_REF}"
