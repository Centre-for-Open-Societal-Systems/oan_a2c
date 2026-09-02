#!/bin/bash
# deploy-ec2.sh - Deploy to AWS EC2 backend instance
# Called by Jenkins for develop branch deployments
set -e

# Required environment variables from Jenkins
: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID is required}"
: "${SSH_KEY:?SSH_KEY is required}"
: "${SSH_USER:?SSH_USER is required}"
: "${BACKEND_IP:?BACKEND_IP is required}"
: "${BUILD_NUMBER:?BUILD_NUMBER is required}"
: "${ECR_REPO:?ECR_REPO is required}"
: "${AWS_REGION:?AWS_REGION is required}"

# Secrets, injected by Jenkins (withCredentials). These are deliberately NOT
# generated here. encryption_key is Frappe's Fernet key for data at rest, so
# minting a fresh one per deploy makes every previously encrypted value
# undecryptable; it has to be stable for the life of the environment.
: "${ENCRYPTION_KEY:?ENCRYPTION_KEY is required - stable per environment, never regenerated}"
: "${SECRET_KEY:?SECRET_KEY is required - HMAC key for consent receipt signatures}"
: "${JWT_SECRETS:?JWT_SECRETS is required - JSON map of kid to secret, e.g. {\"v1\": \"...\"}}"
JWT_CURRENT_KID="${JWT_CURRENT_KID:-v1}"

# JWT_SECRETS is interpolated inside single quotes in the heredoc below, so a
# single quote in the value would break out of them and corrupt the command.
case "${JWT_SECRETS}" in
    *"'"*) echo "JWT_SECRETS must not contain a single quote" >&2; exit 1 ;;
esac

echo "=== Deploying to EC2: ${BACKEND_IP} ==="
echo "=== Image: ${ECR_REPO}:develop-${BUILD_NUMBER} ==="

ssh -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=no \
    "${SSH_USER}@${BACKEND_IP}" << SSHEOF

    set -e
    cd /opt/oan_a2c

    echo "=== Logging in to ECR ==="
    aws ecr get-login-password --region ${AWS_REGION} | \
        docker login --username AWS --password-stdin \
        ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

    echo "=== Pointing .env at ${ECR_REPO}:develop-${BUILD_NUMBER} (rewrites whole line) ==="
    # Rewrite the ENTIRE ECR_IMAGE line rather than sed the tag in place: this migrates
    # the stack from the legacy oan-a2c repo to oan/a2c on the first build, and is
    # robust regardless of what repo/tag the .env currently holds.
    # NOTE: no backticks in this comment -- it lives inside the unquoted <<SSHEOF heredoc,
    # so backticks would be command-substituted on the Jenkins agent (that broke build #23/#24:
    # "oan-a2c: command not found" / "oan/a2c: No such file or directory").
    sed -i "s|^ECR_IMAGE=.*|ECR_IMAGE=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:develop-${BUILD_NUMBER}|" .env

    echo "=== Pulling new image ==="
    docker compose pull

    echo "=== Restarting services ==="
    docker compose up -d --no-deps --force-recreate \
        backend frontend websocket queue-short queue-long scheduler

    sleep 20

    # Do NOT touch sites/assets here. Images built from frappe_docker's
    # images/layered/Containerfile bake assets into the image layer at
    # /home/frappe/frappe-bench/assets, and the image entrypoint symlinks
    # sites/assets -> that path on every container start. Deleting the symlink
    # and running "bench build" breaks the site: /assets/** 404s, and the
    # frontend overrides the entrypoint with nginx-entrypoint.sh, so a frontend
    # restart does not recreate the link.

    echo "=== Setting OpenG2P config ==="
    docker compose exec -T backend bench set-config -g openg2p_base_url "https://socialregistry-22062026.dev.openg2p.test"
    docker compose exec -T backend bench set-config -g openg2p_username "portal_agent"
    docker compose exec -T backend bench set-config -g openg2p_password "portal_agent"
    docker compose exec -T backend bench set-config -g openg2p_db "${OPENG2P_DB:-socialregistry}"

    echo "=== Setting secrets ==="
    # Everything here expands on the Jenkins agent before the heredoc is sent, which
    # is why these must come from injected credentials. A \$(...) in this block would
    # run on the agent rather than on EC2 and silently yield an empty value.
    docker compose exec -T backend bench set-config -g secret_key "${SECRET_KEY}"
    docker compose exec -T backend bench --site mysite.localhost set-config encryption_key "${ENCRYPTION_KEY}"
    docker compose exec -T backend bench --site mysite.localhost set-config jwt_secrets '${JWT_SECRETS}' --parse
    docker compose exec -T backend bench --site mysite.localhost set-config jwt_current_kid "${JWT_CURRENT_KID}"

    echo "=== Running migrations ==="
    docker compose exec -T backend bench --site mysite.localhost migrate
    docker compose exec -T backend bench --site mysite.localhost clear-cache

    echo "=== Restarting frontend ==="
    docker compose restart frontend
    sleep 10

    echo "=== Health check ==="
    # sites/assets must be the symlink the image entrypoint creates on start
    # (-> /home/frappe/frappe-bench/assets in the image layer). nginx serves
    # /assets from it, so if this is missing or empty the site renders unstyled.
    # \$ is escaped: unescaped it would expand on the Jenkins agent, not on EC2.
    docker compose exec -T backend bash -c \
        'test -L sites/assets && test -n "\$(ls -A sites/assets/ 2>/dev/null)"' \
        || { echo "FAIL: sites/assets is not a populated symlink"; exit 1; }

    echo "Health check passed (assets linked)"

    # Reclaim disk: each deploy pulls a fresh oan/a2c:develop-<n> (~3.4GB); without this the
    # box fills up over time and the next docker compose pull fails "no space left on device".
    # -a removes images no running container uses (old app tags) -- the live stacks are untouched.
    # (No backticks here: inside the unquoted <<SSHEOF heredoc they'd run on the Jenkins agent.)
    echo "=== Pruning unused images ==="
    docker image prune -af || true

    echo "=== Deployment complete ==="
    docker compose ps

SSHEOF

echo "=== EC2 deployment finished ==="
