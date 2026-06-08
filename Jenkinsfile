pipeline {
    agent any

    environment {
        AWS_REGION     = 'ap-south-1'
        AWS_ACCOUNT_ID = credentials('AWS_ACCOUNT_ID')
        ECR_REPO       = 'oan-a2c'
        IMAGE_URI      = "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"
        FRAPPE_BRANCH  = 'version-16'
        FRAPPE_PATH    = 'https://github.com/frappe/frappe'
    }

    stages {
        stage('Checkout') {
            steps { checkout scm }
        }

        stage('Build Docker Image') {
            steps {
                sh '''
                    echo '[{"url":"https://github.com/Protean-FOSS-Factory/oan_a2c.git","branch":"develop"}]' > /tmp/apps.json
                    DOCKER_BUILDKIT=1 docker buildx build \
                        --build-arg FRAPPE_PATH=${FRAPPE_PATH} \
                        --build-arg FRAPPE_BRANCH=${FRAPPE_BRANCH} \
                        --secret id=apps_json,src=/tmp/apps.json \
                        --tag ${IMAGE_URI}:1.0.${BUILD_NUMBER} \
                        --tag ${IMAGE_URI}:latest \
                        --file images/layered/Containerfile \
                        --network=host \
                        --load \
                        --no-cache .
                '''
            }
        }

        stage('Push to ECR') {
            steps {
                sh '''
                    aws ecr get-login-password --region ${AWS_REGION} | \
                        docker login --username AWS --password-stdin \
                        ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com
                    docker push ${IMAGE_URI}:1.0.${BUILD_NUMBER}
                    docker push ${IMAGE_URI}:latest
                '''
            }
        }

        stage('Deploy to Backend') {
            steps {
                sshagent(credentials: ['backend-ssh-key']) {
                    sh '''
                        ssh -o StrictHostKeyChecking=no ubuntu@10.0.2.100 "
                            cd /opt/oan_a2c &&
                            aws ecr get-login-password --region ap-south-1 | \
                                docker login --username AWS --password-stdin \
                                ${AWS_ACCOUNT_ID}.dkr.ecr.ap-south-1.amazonaws.com &&
                            docker compose pull &&
                            docker compose up -d --no-deps --force-recreate \
                                backend frontend websocket queue-short queue-long scheduler &&
                            docker compose ps
                        "
                    '''
                }
            }
        }

        stage('Cleanup') {
            steps {
                sh '''
                    docker rmi ${IMAGE_URI}:1.0.${BUILD_NUMBER} || true
                    docker system prune -f || true
                '''
            }
        }
    }

    post {
        success { echo "Build and deploy successful!" }
        failure { echo "Pipeline failed!" }
    }
}
