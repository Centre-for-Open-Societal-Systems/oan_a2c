# oan_dev deploy access

The `develop` pipeline deploys to the oan_dev k3s node from a VPN agent (label `oan-ci-deploy`) with a
kubeconfig, not an SSH key. Stages `develop → oan_dev (helm)` and `develop → gateway (apisix)` run
`ci/deploy-helm-dev.sh` and `ci/deploy-gateway.sh`.

## The identity

`ci/cluster/oan-dev-deployer.yaml` creates ServiceAccount `jenkins-a2c-deployer` in namespace `a2c`:

| Namespace       | Access                                 | Used for                             |
| --------------- | -------------------------------------- | ------------------------------------ |
| `a2c`           | built-in `admin` role                  | the `a2c` and `a2c-gateway` releases |
| `api6-gateway`  | get/list Services                      | find the APISIX release's Redis      |
| `frappe-common` | get Secret `oan-frappe-common-db` only | copy the Postgres password           |

It cannot create namespaces or touch anything else. If a chart adds cluster-wide objects (CRDs,
ClusterRoles), the deploy fails with a forbidden error; grant that one thing in the manifest.

## Set up (once per cluster)

1. On the node (`ssh oan_dev`), with this repo checked out or `ci/cluster/` copied over:
   `bash ci/cluster/bootstrap-oan-dev.sh /tmp/oan-dev.kubeconfig [https://<api-address>:6443]`.
   The address must be reachable from the agent and be in the k3s certificate's names.
2. Jenkins → Manage Jenkins → Credentials, in a scope the `oan-package` folder can see → Add →
   **Secret file**, ID `oan-dev-kubeconfig`, upload the file. Note the creation and rotation dates in the
   description. Delete every local copy of the file.
3. On a shell on the `oan-ci-deploy` agent, check access:
   `KUBECONFIG=<file> kubectl auth can-i create secrets -n a2c` (yes) and `-n default` (no).
   The agent also needs `helm` 3.10+ and `kubectl`. It needs no AWS access.

## Rotate or revoke

- Rotate: delete Secret `jenkins-a2c-deployer-token` in `a2c`, rerun the bootstrap script, replace the
  Jenkins credential.
- Revoke: delete the Jenkins credential and `kubectl delete -f ci/cluster/oan-dev-deployer.yaml`.

Never commit the kubeconfig, its token or `ca.crt`.

## Image pulls (node side)

The node pulls from ECR with its own IAM role, so the deploy keeps no pull Secret and the agent needs no AWS
access. One-time setup on the oan_dev node (done 2026-11-12):

1. Instance profile `oan-services-dev-ecr-pull` on the EC2 instance: `ecr:GetAuthorizationToken`, and
   `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer`, `ecr:BatchCheckLayerAvailability` on the repositories it pulls.
2. `ecr-credential-provider` (cloud-provider-aws, v1.37.0 from `artifacts.k8s.io`) in
   `/var/lib/rancher/credentialprovider/bin/`, with `/var/lib/rancher/credentialprovider/config.yaml`
   (`matchImages: "*.dkr.ecr.*.amazonaws.com"`, `get-credentials`).
3. `/etc/rancher/k3s/config.yaml`: `kubelet-arg` lines `image-credential-provider-config=` and
   `image-credential-provider-bin-dir=` pointing at them, then `systemctl restart k3s`.

A new node needs the same three steps. A pull failing with 401 or `no basic auth credentials` means the
role, the binary or the kubelet args are missing.
