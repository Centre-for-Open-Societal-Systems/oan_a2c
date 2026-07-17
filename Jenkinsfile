pipeline {
    agent any

    environment {
        AWS_REGION    = 'ap-south-1'
        ECR_REPO      = 'oan-a2c'
        FRAPPE_BRANCH = 'version-16'
        FRAPPE_PATH   = 'https://github.com/frappe/frappe'
        BACKEND_IP    = '10.0.2.100'
    }

    stages {
        stage('Checkout') {
            steps { checkout scm }
        }

        stage('Build Docker Image') {
            steps {
                withCredentials([string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID')]) {
                    sh '''
                        IMAGE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"
                        BRANCH="${GIT_BRANCH##*/}"

                        rm -rf frappe_docker
                        git clone https://github.com/frappe/frappe_docker.git frappe_docker

                        echo "[{\"url\":\"https://github.com/Centre-for-Open-Societal-Systems/oan_a2c.git\",\"branch\":\"${BRANCH}\"}]" > /tmp/apps.json

                        echo "Building for branch: ${BRANCH}"

                        cd frappe_docker
                        DOCKER_BUILDKIT=1 docker buildx build \
                            --build-arg FRAPPE_PATH=${FRAPPE_PATH} \
                            --build-arg FRAPPE_BRANCH=${FRAPPE_BRANCH} \
                            --secret id=apps_json,src=/tmp/apps.json \
                            --tag ${IMAGE_URI}:${BRANCH}-${BUILD_NUMBER} \
                            --tag ${IMAGE_URI}:${BRANCH} \
                            --file images/layered/Containerfile \
                            --network=host \
                            --load .

                        echo "Built ${IMAGE_URI}:${BRANCH}-${BUILD_NUMBER}"
                    '''
                }
            }
        }

        stage('Push to ECR') {
            steps {
                withCredentials([string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID')]) {
                    sh '''
                        IMAGE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"
                        BRANCH="${GIT_BRANCH##*/}"

                        aws ecr get-login-password --region ${AWS_REGION} | \
                            docker login --username AWS --password-stdin \
                            ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

                        docker push ${IMAGE_URI}:${BRANCH}-${BUILD_NUMBER}
                        docker push ${IMAGE_URI}:${BRANCH}

                        echo "Pushed ${IMAGE_URI}:${BRANCH}-${BUILD_NUMBER}"

                        echo "=== Cleaning up local images ==="
                        docker rmi ${IMAGE_URI}:${BRANCH}-${BUILD_NUMBER} || true
                        docker system prune -f || true
                        echo "=== Cleanup complete ==="
                    '''
                }
            }
        }

        stage('Deploy') {
            steps {
                withCredentials([
                    string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID'),
                    sshUserPrivateKey(
                        credentialsId: 'backend-ssh-key',
                        keyFileVariable: 'SSH_KEY',
                        usernameVariable: 'SSH_USER'
                    )
                ]) {
                    script {
                        def branch = env.GIT_BRANCH.replaceAll('.*/','')
                        echo "Deploying branch: ${branch}"

                        if (branch == 'develop') {
                            echo "=== Running deploy-ec2.sh for develop branch ==="
                            sh '''
                                chmod +x ci/deploy-ec2.sh
                                AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID} \
                                SSH_KEY=${SSH_KEY} \
                                SSH_USER=${SSH_USER} \
                                BACKEND_IP=${BACKEND_IP} \
                                BUILD_NUMBER=${BUILD_NUMBER} \
                                ECR_REPO=${ECR_REPO} \
                                AWS_REGION=${AWS_REGION} \
                                bash ci/deploy-ec2.sh
                            '''
                        } else if (branch == 'main') {
                            echo "=== Running deploy-onprem.sh for main branch ==="
                            sh '''
                                chmod +x ci/deploy-onprem.sh
                                AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID} \
                                BUILD_NUMBER=${BUILD_NUMBER} \
                                ECR_REPO=${ECR_REPO} \
                                AWS_REGION=${AWS_REGION} \
                                bash ci/deploy-onprem.sh
                            '''
                        } else {
                            echo "Branch ${branch} — skipping deployment"
                        }
                    }
                }
            }
        }
    }

    post {
        success { echo "Pipeline successful! Branch: ${GIT_BRANCH} Build: ${BUILD_NUMBER}" }
        failure { echo "Pipeline failed! Branch: ${GIT_BRANCH} Build: ${BUILD_NUMBER}" }
    }
}