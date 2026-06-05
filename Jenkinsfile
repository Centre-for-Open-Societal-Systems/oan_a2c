//Build, migrate data and deploy to staging environment on AWS EC2 instance
pipeline {
    agent any

    environment {
        AWS_REGION     = 'ap-south-1'
        ECR_REPO       = 'oan-a2c'
        FRAPPE_BRANCH  = 'version-16'
        FRAPPE_PATH    = 'https://github.com/frappe/frappe'
        BACKEND_IP     = '10.0.2.100'
    }

    stages {
        stage('Checkout') {
            steps { checkout scm }
        }

        stage('Build Docker Image') {
            steps {
                withCredentials([string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID')]) {
                    sh '''
                        rm -rf frappe_docker
                        git clone https://github.com/frappe/frappe_docker.git frappe_docker

                        echo '[{"url":"https://github.com/Centre-for-Open-Societal-Systems/oan_a2c.git","branch":"develop"}]' > /tmp/apps.json

                        IMAGE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"

                        cd frappe_docker
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
        }

        stage('Push to ECR') {
            steps {
                withCredentials([string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID')]) {
                    sh '''
                        IMAGE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"

                        aws ecr get-login-password --region ${AWS_REGION} | \
                            docker login --username AWS --password-stdin \
                            ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

                        docker push ${IMAGE_URI}:1.0.${BUILD_NUMBER}
                        docker push ${IMAGE_URI}:latest

                        echo "Pushed ${IMAGE_URI}:1.0.${BUILD_NUMBER}"
                    '''
                }
            }
        }

        stage('Deploy to AWS Staging') {
            steps {
                withCredentials([
                    string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID'),
                    sshUserPrivateKey(credentialsId: 'backend-ssh-key', keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER')
                ]) {
                    sh '''
                        ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no ${SSH_USER}@${BACKEND_IP} bash << SSHEOF
                            set -e
                            cd /opt/oan_a2c

                            echo "=== Pulling new image ==="
                            aws ecr get-login-password --region ap-south-1 | \
                                docker login --username AWS --password-stdin \
                                ${AWS_ACCOUNT_ID}.dkr.ecr.ap-south-1.amazonaws.com

                            docker compose pull

                            echo "=== Restarting services ==="
                            docker compose up -d --no-deps --force-recreate \
                                backend frontend websocket queue-short queue-long scheduler

                            echo "=== Waiting for backend to be ready ==="
                            sleep 20

                            echo "=== Clearing stale assets ==="
                            docker compose exec -T backend bash -c "
                                rm -rf /home/frappe/frappe-bench/sites/assets
                            "

                            echo "=== Rebuilding assets ==="
                            docker compose exec -T backend bench build --force

                            echo "=== Running migrations ==="
                            docker compose exec -T backend bench --site mysite.localhost migrate

                            echo "=== Clearing cache ==="
                            docker compose exec -T backend bench --site mysite.localhost clear-cache
                            docker compose exec -T backend bench --site mysite.localhost clear-website-cache

                            echo "=== Restarting frontend to serve new assets ==="
                            docker compose restart frontend

                            echo "=== Waiting for frontend ==="
                            sleep 10

                            echo "=== Health check ==="
                            for i in \$(seq 1 10); do
                                if curl -sf http://localhost:8080/health > /dev/null 2>&1; then
                                    echo "Health check passed!"
                                    break
                                fi
                                echo "Attempt \$i failed, retrying in 5s..."
                                sleep 5
                            done

                            echo "=== Deployment complete ==="
                            docker compose ps
SSHEOF
                    '''
                }
            }
        }

        stage('Cleanup Jenkins') {
            steps {
                withCredentials([string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID')]) {
                    sh '''
                        IMAGE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"
                        docker rmi ${IMAGE_URI}:1.0.${BUILD_NUMBER} || true
                        docker system prune -f || true
                    '''
                }
            }
        }
    }

    post {
        success {
            echo "Build and deploy successful! Version: 1.0.${BUILD_NUMBER}"
        }
        failure {
            echo "Pipeline failed!"
        }
    }
}
