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

    echo "=== Updating image tag in .env ==="
    sed -i "s|oan-a2c:.*|oan-a2c:develop-${BUILD_NUMBER}|" .env

    echo "=== Pulling new image ==="
    docker compose pull

    echo "=== Restarting services ==="
    docker compose up -d --no-deps --force-recreate \
        backend frontend websocket queue-short queue-long scheduler

    sleep 20

    echo "=== Clearing stale assets ==="
    docker compose exec -T backend bash -c "rm -rf /home/frappe/frappe-bench/sites/assets"

    echo "=== Rebuilding assets ==="
    docker compose exec -T backend bench build --force
    docker compose exec -T backend bench --site mysite.localhost migrate
    docker compose exec -T backend bench --site mysite.localhost clear-cache

    echo "=== Setting OpenG2P config ==="
    docker compose exec -T backend bench set-config -g openg2p_base_url "https://socialregistry-22062026.dev.openg2p.test"
    docker compose exec -T backend bench set-config -g openg2p_username "portal_agent"
    docker compose exec -T backend bench set-config -g openg2p_password "portal_agent"
    docker compose exec -T backend bench set-config -g openg2p_db "${OPENG2P_DB:-socialregistry}"
    docker compose exec -T backend bench set-config -g secret_key "dummy-secret-key-change-me-12345"

    echo "=== Running migrations ==="
    docker compose exec -T backend bench --site mysite.localhost migrate

    echo "=== Setting encryption key ==="
    ENCRYPTION_KEY=$(docker compose exec -T backend python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" | tr -d '\r\n')
    docker compose exec -T backend bench --site mysite.localhost set-config encryption_key "${ENCRYPTION_KEY}"

    echo "=== Restarting frontend ==="
    docker compose restart frontend
    sleep 10

    echo "=== Health check ==="
    curl -sf http://localhost:8080/health && echo "Health check passed!" || echo "Warning: health check failed"

    echo "=== Deployment complete ==="
    docker compose ps

SSHEOF

echo "=== EC2 deployment finished ==="