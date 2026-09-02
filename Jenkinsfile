// =============================================================================
//  Jenkinsfile — oan_a2c (Frappe app). Single pipeline for the `oan-package`
//  GitHub Organization folder (multibranch).
//
//  Per branch (all publish to the namespaced ECR repo `oan/a2c`):
//    develop     -> build + push + ci/deploy-ec2.sh
//                   (EC2 docker-compose deploy to BACKEND_IP:/opt/oan_a2c)
//    staging_aws -> build + push + SSH docker-compose deploy to a SEPARATE stack on
//                   the SAME box (BACKEND_IP:${STAGING_APP_DIR}, compose project
//                   oan_a2c_staging). Coexists with develop's /opt/oan_a2c stack.
//    staging_ati -> build + push + ci/update-kustomize-ati.sh
//                   (GitOps: bump oan-kustomize `staging` overlay; ArgoCD on node 41 syncs)
//
//  develop + staging_aws were migrated off the legacy `oan-a2c` repo onto `oan/a2c`
//  (staging_ati was already there). Both deploy scripts rewrite the WHOLE ECR_IMAGE
//  line in the box `.env`, so the first build after this change repoints the stack
//  automatically — no manual .env edit needed.
//  `main` is handled separately by Jenkinsfile.main (retained during validation).
//
//  NOTE (staging_aws prereq): ${STAGING_APP_DIR} must be provisioned on BACKEND_IP
//  before the first staging_aws build — a copy of /opt/oan_a2c with its own .env
//  (ECR_IMAGE on oan/a2c) and DISTINCT host ports (dev uses frontend 8080 and
//  kong 8000-8001; give staging e.g. 8090 and 8010-8011) plus its own site, so the
//  two stacks don't collide. Same box, different image tag + different compose app.
//
//  Tags:  <branch>-<build>   immutable, pinned by oan-kustomize / deploy-ec2.sh
//         <branch>-latest    moving alias (convenience)
//
//  Agent needs: docker(+buildx), aws cli v2, git, kustomize.
//  Credentials: AWS_ACCOUNT_ID (string), backend-ssh-key (ssh), oan-deployer (GitHub App).
//  NOTE: `aws ecr get-login-password` uses the agent's ambient AWS identity, which
//        must have ECR push on `oan/a2c`.
// =============================================================================
pipeline {
  agent any

  options {
    timestamps()
    disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '30'))
    timeout(time: 60, unit: 'MINUTES')
  }

  environment {
    AWS_REGION      = 'ap-south-1'
    FRAPPE_BRANCH   = 'version-16'
    FRAPPE_PATH     = 'https://github.com/frappe/frappe'
    BACKEND_IP      = '10.0.2.100'
    STAGING_APP_DIR = '/opt/oan_a2c_staging'   // staging_aws stack (separate from /opt/oan_a2c)
  }

  stages {
    stage('Resolve') {
      steps {
        script {
          // All branches publish to the namespaced repo now (develop + staging_aws
          // migrated off legacy `oan-a2c`; staging_ati was already here).
          env.ECR_REPO      = 'oan/a2c'
          // staging_ati / staging_aws publish under hyphenated `staging-ati-` / `staging-aws-`
          // prefixes (not the branch-derived `staging_ati-` / `staging_aws-`) so the immutable
          // tags read staging-ati-<build> / staging-aws-<build>, uniform across all repos.
          // develop keeps its branch-name tag.
          def tagPrefix     = (env.BRANCH_NAME == 'staging_ati') ? 'staging-ati'
                            : (env.BRANCH_NAME == 'staging_aws') ? 'staging-aws'
                            : env.BRANCH_NAME
          env.IMMUTABLE_TAG = "${tagPrefix}-${env.BUILD_NUMBER}"
          env.MOVING_TAG    = "${tagPrefix}-latest"
          echo "branch=${env.BRANCH_NAME}  repo=${env.ECR_REPO}  tag=${env.IMMUTABLE_TAG}"
        }
      }
    }

    stage('Build image') {
      when { anyOf { branch 'develop'; branch 'staging_aws'; branch 'staging_ati' } }
      steps {
        withCredentials([string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID')]) {
          sh '''#!/usr/bin/env bash
            set -euo pipefail
            IMAGE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"

            rm -rf frappe_docker
            git clone --depth 1 https://github.com/frappe/frappe_docker.git frappe_docker

            printf '[{"url":"https://github.com/Centre-for-Open-Societal-Systems/oan_a2c.git","branch":"%s"}]' \
              "${BRANCH_NAME}" > /tmp/apps.json

            cd frappe_docker
            DOCKER_BUILDKIT=1 docker buildx build \
              --build-arg FRAPPE_PATH=${FRAPPE_PATH} \
              --build-arg FRAPPE_BRANCH=${FRAPPE_BRANCH} \
              --secret id=apps_json,src=/tmp/apps.json \
              --tag ${IMAGE_URI}:${IMMUTABLE_TAG} \
              --tag ${IMAGE_URI}:${MOVING_TAG} \
              --file images/layered/Containerfile \
              --network=host --load .
            echo "Built ${IMAGE_URI}:${IMMUTABLE_TAG}"
          '''
        }
      }
    }

    stage('Push to ECR') {
      when { anyOf { branch 'develop'; branch 'staging_aws'; branch 'staging_ati' } }
      steps {
        withCredentials([string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID')]) {
          sh '''#!/usr/bin/env bash
            set -euo pipefail
            REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
            IMAGE_URI="${REGISTRY}/${ECR_REPO}"

            aws ecr get-login-password --region ${AWS_REGION} \
              | docker login --username AWS --password-stdin "${REGISTRY}"

            docker push ${IMAGE_URI}:${IMMUTABLE_TAG}
            docker push ${IMAGE_URI}:${MOVING_TAG}       # same digest -> ECR dedups

            # Scoped cleanup ONLY. Never `docker system prune -f` on a shared agent:
            # it wipes other jobs' caches/images and can break concurrent builds.
            docker rmi ${IMAGE_URI}:${IMMUTABLE_TAG} ${IMAGE_URI}:${MOVING_TAG} || true
            echo "Pushed ${IMAGE_URI}:${IMMUTABLE_TAG} (+ ${MOVING_TAG})"
          '''
        }
      }
    }

    // ---------------------- per-branch deploy ----------------------

    stage('develop → EC2') {
      when { branch 'develop' }
      steps {
        withCredentials([
          string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID'),
          sshUserPrivateKey(credentialsId: 'backend-ssh-key',
                            keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER'),
          string(credentialsId: 'encryption_key_develop', variable: 'ENCRYPTION_KEY'),
          string(credentialsId: 'secret_key_develop', variable: 'SECRET_KEY'),
          string(credentialsId: 'jwt_secrets_develop', variable: 'JWT_SECRETS')
        ]) {
          sh '''#!/usr/bin/env bash
            set -euo pipefail
            chmod +x ci/deploy-ec2.sh
            AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID} SSH_KEY=${SSH_KEY} SSH_USER=${SSH_USER} \
            BACKEND_IP=${BACKEND_IP} BUILD_NUMBER=${BUILD_NUMBER} \
            ECR_REPO=${ECR_REPO} AWS_REGION=${AWS_REGION} \
            ENCRYPTION_KEY="${ENCRYPTION_KEY}" SECRET_KEY="${SECRET_KEY}" \
            JWT_SECRETS="${JWT_SECRETS}" \
            bash ci/deploy-ec2.sh
          '''
        }
      }
    }

    // staging_aws -> a SEPARATE docker-compose stack on the SAME backend box.
    // Same image build as develop (layered Containerfile bakes assets), pushed to
    // oan/a2c:<staging_aws-build>. Deploys into ${STAGING_APP_DIR} (compose project
    // oan_a2c_staging) so it coexists with develop's /opt/oan_a2c stack — provided
    // that dir is pre-provisioned with its own .env + DISTINCT host ports (see header).
    // Assets are baked into the image and symlinked by the entrypoint, so we do NOT
    // rm sites/assets or `bench build` here — doing so 404s /assets (see deploy-ec2.sh).
    stage('staging_aws → AWS backend (separate stack)') {
      when { branch 'staging_aws' }
      steps {
        withCredentials([
          string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID'),
          sshUserPrivateKey(credentialsId: 'backend-ssh-key',
                            keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER')
        ]) {
          sh '''#!/usr/bin/env bash
            set -euo pipefail
            REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
            ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${SSH_USER}@${BACKEND_IP}" << SSHEOF
              set -e
              cd ${STAGING_APP_DIR}

              echo "=== ECR login ==="
              aws ecr get-login-password --region ${AWS_REGION} \
                | docker login --username AWS --password-stdin ${REGISTRY}

              echo "=== Point .env at ${ECR_REPO}:${IMMUTABLE_TAG} (rewrites whole line) ==="
              sed -i "s|^ECR_IMAGE=.*|ECR_IMAGE=${REGISTRY}/${ECR_REPO}:${IMMUTABLE_TAG}|" .env

              echo "=== Pull + recreate frappe services ==="
              docker compose pull
              docker compose up -d --no-deps --force-recreate \
                backend frontend websocket queue-short queue-long scheduler
              sleep 20

              echo "=== Migrate + clear cache (assets are baked, do NOT rebuild) ==="
              docker compose exec -T backend bench --site mysite.localhost migrate
              docker compose exec -T backend bench --site mysite.localhost clear-cache
              docker compose restart frontend
              sleep 10

              echo "=== Health check (assets symlink present) ==="
              docker compose exec -T backend bash -c 'test -L sites/assets' \
                && echo "Health check passed (assets linked)" \
                || { echo "FAIL: sites/assets is not a symlink"; exit 1; }
              docker compose ps
SSHEOF
          '''
        }
      }
    }

    // staging -> GitOps: bump the oan-kustomize `staging` overlay to the new image.
    // Auth is the `oan-deployer` GitHub App (contents:write on oan-kustomize only);
    // gitUsernamePassword mints a short-lived installation token. All kustomize
    // logic lives in ci/update-kustomize-ati.sh.
    stage('staging → GitOps (ArgoCD@41)') {
      when { branch 'staging_ati' }
      steps {
        withCredentials([
          string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID'),
          gitUsernamePassword(credentialsId: 'oan-deployer', gitToolName: 'Default')
        ]) {
          sh '''#!/usr/bin/env bash
            set -euo pipefail
            chmod +x ci/update-kustomize-ati.sh
            # args: <overlay> <kustomize image match-name> <new image ref>
            ci/update-kustomize-ati.sh staging oan-a2c \
              "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:${IMMUTABLE_TAG}"
          '''
        }
      }
    }
  }

  post {
    success { echo "OK  ${env.BRANCH_NAME} #${env.BUILD_NUMBER} -> ${env.IMMUTABLE_TAG}" }
    failure { echo "FAIL ${env.BRANCH_NAME} #${env.BUILD_NUMBER}" }
  }
}
