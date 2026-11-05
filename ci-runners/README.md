# Oracle on-demand GitHub Actions runners

Infrastructure tracking: https://github.com/ChayzX/k8s-homelab/issues/443

Oracle's existing ARM64 k3s cluster runs ARC0.15.1. Eight repository-scoped scale sets serve the former MinecraftMachine Linux runner registrations. Each has zero idle workers and at most one requested worker. The shared `arc-runners/oracle-ci-global` ResourceQuota admits **one worker pod across the entire fleet**. Other accepted jobs wait; ARC0.15.1 retries quota-denied pod creation every30seconds.

## Repositories and routing

| Repository | Scale set | Labels in addition to its name |
|---|---|---|
| ai-tiktok-agent | oracle-ci-ai-tiktok-agent | self-hosted, Linux, ARM64, homelab-ci, oracle-arc |
| aios | oracle-ci-aios | same |
| cartwise | oracle-ci-cartwise | same |
| k8s-homelab | oracle-ci-k8s-homelab | same, k8s-homelab |
| Operations-ios-app | oracle-ci-operations-ios-app | same |
| pantry-bot | oracle-ci-pantry-bot | same, home-deploy, pantrybot-ci |
| test | oracle-ci-test | same |
| vtube-input-bridge | oracle-ci-vtube-input-bridge | same |

Existing `[self-hosted, homelab-ci]` selectors continue to work. The runners honestly advertise ARM64; they do not advertise X64. Hosted `ubuntu-latest` jobs were not rerouted by this change. Pantry's existing `oracle-arm64` deployment runner and external Windows-specific runners remain unchanged. `home-deploy` is a retained routing alias, not a grant of host access or deployment credentials.

No source changes are required for the four repositories without default-branch jobs targeting their old runner. Their registrations are replaced for future matching jobs. Workflows explicitly requiring x86 Linux need a deliberate port; do not add a false X64 label.

## Resource and access boundaries

- One fleet worker at a time, limited to1CPU and2GiB RAM/1500MiB temporary storage. VTube uses3GiB RAM/4.5GiB storage for its existing Rust/.NET/cross-compilation workflow.
- Work directories are bounded emptyDir volumes; no persistent claims, host mounts, host Docker socket, host network, or Kubernetes service-account token are supplied to workers.
- Standard workers disallow privilege escalation and drop all capabilities. VTube alone allows the official image's container-local sudo because its existing workflow installs cross-compilers. Its pre-job hook installs native build prerequisites. This does not grant host mounts or privileged-container mode.
- Jobs have a1hour pod deadline. Repositories should set smaller per-job workflow timeouts as appropriate.
- Before executing a job, the hook fails if root free space is below3GiB. VTube requires7.5GiB free before its build starts to start with3GiB headroom beyond its4.5GiB storage allowance. This is a preflight check, not a reserved filesystem allocation; kubelet enforcement is asynchronous and other workloads can consume space. Worker storage limits do not bound cached container images or production database growth. Oracle's fixed45GB filesystem must be monitored; no disk expansion or blanket cache/data cleanup is part of this setup.
- Worker network policy permits cluster DNS and public IPv4 HTTP/HTTPS; private address ranges, cloud metadata, and incoming connections are denied. NetworkPolicy does not provide a separate VM boundary or guarantee isolation from the hosting node.
- ARC controller plus eight idle listeners remain running. They used about83MiB combined during rollout; workers scale to zero.
- The existing Oracle GitHub CLI login is held in `arc-runners/oracle-ci-github`. No token value is in these files or Helm values. Keep it valid; a dedicated repository-scoped credential/App is preferred when arranging long-term credential rotation.

## Source files

`values-*.json` are Helm values; JSON is valid YAML. `namespace.json`, `control-quota.json`, `quota.json`, `hooks.json`, and `network-policy.json` are Kubernetes resources. `controller-values.yaml` configures the shared controller. Do not apply the entire directory as Kubernetes manifests because values files are not Kubernetes objects.

Current on-host files: `/home/ubuntu/arc-oracle-fleet` on SSH alias `oracle`. Helm binary and pinned chart archives are under `/home/ubuntu/arc-oracle-pilot`. The old `arc-test` pilot release/namespace was retired after fleet routing passed; its workflow in ChayzX/test now selects the fleet labels. Old pilot quota files are superseded by this directory's control-quota.json.

## Apply configuration

Use Helm3 and the existing administrative kubeconfig. Create the GitHub secret separately from a protected credential source; never put its value into Git or a Helm command argument. On the configured Oracle host:

```bash
helm_bin=/home/ubuntu/arc-oracle-pilot/bin/helm
sudo kubectl apply --server-side -f namespace.json
sudo kubectl apply --server-side -f control-quota.json -f quota.json -f hooks.json -f network-policy.json
sudo "$helm_bin" upgrade --install arc-controller \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller \
  --version 0.15.1 --namespace arc-systems --create-namespace \
  --kubeconfig /etc/rancher/k3s/k3s.yaml -f controller-values.yaml --wait
for values in values-*.json; do
  release=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runnerScaleSetName"])' "$values")
  sudo "$helm_bin" upgrade --install "$release" \
    oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set \
    --version 0.15.1 --namespace arc-runners \
    --kubeconfig /etc/rancher/k3s/k3s.yaml -f "$values" --wait
done
```

The existing controller namespace must exist before applying its quota. For a completely new cluster create arc-systems first. The configured cluster already has the shared controller and namespace. Do not reinstall or delete that controller as a per-repository operation.

## Verify and operate

```bash
sudo kubectl get autoscalingrunnersets -n arc-runners
sudo kubectl get pods -n arc-systems
sudo kubectl get pods -n arc-runners
sudo kubectl get resourcequota -n arc-runners
sudo kubectl top pods -n arc-systems
df -h /
```

All eight sets should be Running/min0/max1, eight listeners and one controller healthy, and zero worker pods while idle. During a job there is one worker; quota waiting messages for other repositories are expected. A failed job is still cleaned up; inspect its real failure rather than interpreting zero workers as success.

Manual fleet routing check (GitHub CLI login requires workflow scope):

```bash
gh workflow run oracle-arc-pilot.yml --repo ChayzX/test --ref main -f pilot_id=fleet-check
gh run list --repo ChayzX/test --workflow oracle-arc-pilot.yml --limit 3
```

## MinecraftMachine retirement and rollback

All nine `actions.runner.ChayzX-*` services on MinecraftMachine were drained, stopped, and disabled. Registrations and directories were retained. Their custom labels were changed to `oracle-migration-draining` so an accidental service start cannot take normal homelab-ci work.

Exact pre-cutover labels/IDs and service states are saved under `/home/chase/runner-migration-20261104/` on MinecraftMachine. If rollback is needed, first stop accepting new jobs on the affected Oracle set and let its active job finish; restore the affected runner's original custom labels through GitHub's runner-label API, then enable/start only its exact saved systemd unit. Do not simultaneously restore old labels and leave an incompatible Oracle pool accepting the same jobs.

AiSOC is a separate deployment on MinecraftMachine; do not restart GitHub runner services to troubleshoot it. Source backups and rollback material intentionally contain no GitHub credential values.
