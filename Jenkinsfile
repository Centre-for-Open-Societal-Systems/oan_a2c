// =============================================================================
//  Jenkinsfile — oan_a2c (Frappe app). Single pipeline for the `oan-package`
//  GitHub Organization folder (multibranch).
//
//  Per branch:
//    develop -> build + push to ECR (oan-a2c) + ci/deploy-ec2.sh
//               (existing EC2 docker-compose deploy — UNCHANGED)
//    staging -> build + push to ECR (oan/a2c) + ci/update-kustomize.sh
//               (GitOps: bump oan-kustomize `staging` overlay; ArgoCD on node 41 syncs)
//
//  develop intentionally still targets the LEGACY `oan-a2c` repo because
//  ci/deploy-ec2.sh + the EC2 instance `.env` reference `oan-a2c`. Migrating
//  develop to `oan/a2c` is a follow-up (needs the instance `.env` updated).
//  `main` is handled separately by Jenkinsfile.main (retained during validation).
//
//  Tags:  <branch>-<build>   immutable, pinned by oan-kustomize / deploy-ec2.sh
//         <branch>-latest    moving alias (convenience)
//
//  Agent needs: docker(+buildx), aws cli v2, git, kustomize.
//  Credentials: AWS_ACCOUNT_ID (string), backend-ssh-key (ssh), oan-deployer (GitHub App).
//  NOTE: `aws ecr get-login-password` uses the agent's ambient AWS identity, which
//        must have ECR push on both `oan-a2c` and `oan/*`.
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
    AWS_REGION    = 'ap-south-1'
    FRAPPE_BRANCH = 'version-16'
    FRAPPE_PATH   = 'https://github.com/frappe/frappe'
    BACKEND_IP    = '10.0.2.100'
  }

  stages {
    stage('Resolve') {
      steps {
        script {
          // staging -> new namespaced repo; everything else -> legacy repo (unchanged).
          env.ECR_REPO      = (env.BRANCH_NAME == 'staging') ? 'oan/a2c' : 'oan-a2c'
          env.IMMUTABLE_TAG = "${env.BRANCH_NAME}-${env.BUILD_NUMBER}"
          env.MOVING_TAG    = "${env.BRANCH_NAME}-latest"
          echo "branch=${env.BRANCH_NAME}  repo=${env.ECR_REPO}  tag=${env.IMMUTABLE_TAG}"
        }
      }
    }

    stage('Build image') {
      when { anyOf { branch 'develop'; branch 'staging' } }
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
      when { anyOf { branch 'develop'; branch 'staging' } }
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
                            keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER')
        ]) {
          sh '''#!/usr/bin/env bash
            set -euo pipefail
            chmod +x ci/deploy-ec2.sh
            AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID} SSH_KEY=${SSH_KEY} SSH_USER=${SSH_USER} \
            BACKEND_IP=${BACKEND_IP} BUILD_NUMBER=${BUILD_NUMBER} \
            ECR_REPO=${ECR_REPO} AWS_REGION=${AWS_REGION} \
            bash ci/deploy-ec2.sh
          '''
        }
      }
    }

    // staging -> GitOps: bump the oan-kustomize `staging` overlay to the new image.
    // Auth is the `oan-deployer` GitHub App (contents:write on oan-kustomize only);
    // gitUsernamePassword mints a short-lived installation token. All kustomize
    // logic lives in ci/update-kustomize.sh.
    stage('staging → GitOps (ArgoCD@41)') {
      when { branch 'staging' }
      steps {
        withCredentials([
          string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID'),
          gitUsernamePassword(credentialsId: 'oan-deployer', gitToolName: 'Default')
        ]) {
          sh '''#!/usr/bin/env bash
            set -euo pipefail
            chmod +x ci/update-kustomize.sh
            # args: <overlay> <kustomize image match-name> <new image ref>
            ci/update-kustomize.sh staging oan-a2c \
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
