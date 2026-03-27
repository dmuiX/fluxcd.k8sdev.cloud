# 🚀 Production Grade K8s Cluster — infra & apps deployed automatically via FluxCD

A GitOps-managed Kubernetes playground cluster using [FluxCD](https://fluxcd.io/) — built to learn Kubernetes and get hands-on experience with running a production-grade cluster. This repo serves as a reference for a fully automated cluster setup with secrets management, observability, storage, and networking — no manual kubectl apply after bootstrapping.

The cluster itself is built with Talos Linux and provisioned in [k8s-cluster-talos](https://github.com/dmuiX/k8s-cluster-talos).

## 🌐 Why Cilium for everything?

One component that covers the full networking stack — no need to combine multiple tools:

- **CNI** — pod networking
- **kube-proxy replacement** — eBPF-based, lower overhead
- **L2 Announcements** — announces LoadBalancer IPs via ARP to the local network, replaces MetalLB
- **Gateway API** — ingress/routing without a separate ingress controller

## 🔐 Why OpenBao over Sealed Secrets?

Sealed Secrets encrypts secrets per-cluster and stores the ciphertext in Git. OpenBao (open-source Vault fork) keeps secrets completely out of Git and provides a central UI to manage them. The tradeoff is more setup complexity upfront, but day-to-day handling is simpler — add or update a secret in one place, External Secrets syncs it into the cluster automatically. External Secrets is the bridge that makes this work: it reads from OpenBao and creates the Kubernetes Secrets that workloads actually consume.

This repo went through both alternatives first: started with HashiCorp Vault, but switched after HashiCorp moved it to a paid/BSL license — OpenBao is the community fork that stayed open-source. Also tried Sealed Secrets along the way — works fine, but having a central UI and managing secrets completely outside of Git won in the end.

Setting up OpenBao on Kubernetes has quite a few gotchas (raft config, auto-unseal, HTTP vs HTTPS mismatches...). If you're doing this yourself: **[read the docs first](docs/openbao.md)**.

## 💾 Why Longhorn?

On-prem cluster running on VMs — no cloud storage provider available. Longhorn is the simplest way to expose local node storage as a proper CSI-backed StorageClass with replication across nodes. No external storage infrastructure needed.

## 📊 Why kube-prometheus-stack?

The standard solution for Kubernetes monitoring. Prometheus + Grafana + Alertmanager in one Helm chart, with pre-built dashboards for the whole cluster out of the box.

## 🔒 Why cert-manager and External DNS?

Two annotations on a Gateway and you get a DNS record and a valid TLS certificate — fully automated, fully GitOps. No manual DNS or certificate management needed when deploying a new app.

## 🗄️ Why CloudNative PG and Redis Operator?

Declarative database provisioning via GitOps — define a PostgreSQL cluster or Redis instance as a YAML manifest and the operator handles the rest. No manual database setup outside of Git.

## 🔄 Why FluxCD over ArgoCD?

- Lower memory footprint
- Great GUI integration via [Headlamp](https://headlamp.dev/) (FluxCD plugin) and the [Weaveworks GitOps VSCode extension](https://marketplace.visualstudio.com/items?itemName=Weaveworks.vscode-gitops-tools)
- ArgoCD got annoying

## 🧱 Stack

| Component | Purpose | Why |
| --------- | ------- | --- |
| [FluxCD](https://fluxcd.io/) | GitOps continuous delivery | Declarative, pull-based — cluster reconciles itself from this repo |
| [Cilium](https://cilium.io/) | CNI, L2 LoadBalancer, Gateway API | Replaces kube-proxy, handles L2 LB for bare-metal LoadBalancer IPs, and serves as the Gateway API implementation |
| [cert-manager](https://cert-manager.io/) | TLS certificates via Let's Encrypt | DNS-01 challenge via Cloudflare — works regardless of which nameservers the rest of the cluster uses |
| [Longhorn](https://longhorn.io/) | Distributed block storage | Replicated block storage for stateful workloads |
| [OpenBao](https://openbao.org/) | Secrets management | Open-source Vault fork — stores all secrets; auto-unseal via static key so the cluster recovers after restarts without manual intervention |
| [External Secrets](https://external-secrets.io/) | Sync secrets from OpenBao into Kubernetes | Secrets live in OpenBao, not in this repo; External Secrets syncs them into Kubernetes at runtime |
| [External DNS](https://github.com/kubernetes-sigs/external-dns) | Automatic DNS records | Creates DNS entries in Pi-hole automatically when a Gateway or Service is created |
| [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack) | Prometheus + Grafana monitoring | Full cluster observability out of the box |
| [Loki](https://grafana.com/oss/loki/) | Log aggregation | SingleBinary mode with filesystem storage on Longhorn — queried through Grafana |
| [Grafana Alloy](https://grafana.com/oss/alloy/) | Log collector | DaemonSet that collects logs from all pods and ships them to Loki |
| [Flagger](https://flagger.app/) | Progressive delivery | Canary deployments with automated rollback based on Hubble HTTP metrics |
| [OpenCost](https://www.opencost.io/) | Kubernetes cost monitoring | Track resource cost per workload |
| [CloudNative PG](https://cloudnative-pg.io/) | PostgreSQL operator | Manages PostgreSQL clusters declaratively |
| [Redis Operator](https://github.com/spotahome/redis-operator) | Redis operator | Manages Redis instances declaratively |

## 📦 Apps

| App | Stack | Why |
| --- | ----- | --- |
| [Nextcloud](https://nextcloud.com/) | PostgreSQL (CloudNative PG) + Redis | Self-hosted file sync, but mainly to test CloudNative PG and Redis Operator in practice |
| [podinfo](https://github.com/stefanprodan/podinfo) | Flagger Canary | Smoke test with progressive delivery — verifies DNS, certs, routing, canary deployments, and the overall stack work end-to-end |

## 📁 Repository Structure

```text
clusters/        # Flux entrypoint — reconciles infra and apps
infra/
  controller/    # Helm releases for infrastructure components
  config/        # Config that depends on controllers (issuers, gateways, secret stores...)
apps/            # Application Helm releases
docs/            # Notes and setup guides for specific components
```

Flux reconciles three Kustomizations in strict order via `dependsOn`:

```text
infra-controller → infra-config → apps
```

`infra-controller` and `infra-config` are split because config resources (e.g. `ClusterIssuer`, `ClusterSecretStore`, Gateways) depend on the CRDs that controllers install. Without the split, Flux would try to apply config before the CRDs exist and fail.

## 📋 Deployment Order

Flux deploys infrastructure in dependency order:

1. cert-manager
2. Cilium
3. Longhorn
4. OpenBao
5. External Secrets
6. kube-prometheus-stack
7. Loki
8. Grafana Alloy
9. Flagger + Loadtester
10. OpenCost
11. CloudNative PG
12. Redis Operator

External DNS is deployed independently outside this order — it requires a `pihole` secret that must be created manually before bootstrapping (see [docs/external-dns.md](docs/external-dns.md)).

## 🔑 Secrets Strategy

No secrets are stored in this repo. The flow is:

1. **OpenBao** holds all secrets (Cloudflare API token, app passwords, etc.)
2. **External Secrets** reads from OpenBao and creates Kubernetes Secrets at runtime
3. **cert-manager** uses the Cloudflare token secret for DNS-01 challenges
4. **External DNS** uses a manually bootstrapped secret for Pi-hole access (chicken-and-egg: External Secrets can't run before OpenBao is up)

OpenBao uses a static key for auto-unseal stored as a Kubernetes Secret — the cluster fully recovers after restarts without manual unsealing. See [docs/openbao.md](docs/openbao.md) for the full setup guide.

## 💡 Learnings

Things that broke and why — in case this repo saves someone else the debugging time.

### Don't set CPU limits on infrastructure components

Added CPU limits to everything early on — Longhorn and Prometheus kept hanging and getting throttled, especially under load. Infrastructure components have spiky CPU usage. Requests are fine, limits are not. Removed all CPU limits, kept only memory requests.

### HelmRelease dependency order matters

Initially all HelmReleases deployed in parallel — controllers collided and failed. Added `dependsOn` between them so only one deploys at a time. Later simplified by relying on the Kustomization-level `dependsOn` chain (`infra-controller → infra-config → apps`) instead of chaining every single HelmRelease.

### cert-manager DNS-01 needs recursive nameservers

cert-manager couldn't validate DNS-01 challenges because it was using the cluster's internal DNS, which couldn't reach the authoritative nameserver for the domain. Fix: set `dns01RecursiveNameservers: "1.1.1.1:53,9.9.9.9:53"` in the cert-manager Helm values so it always queries Cloudflare directly.

### OpenBao: don't pass the unseal key via environment variable

Tried passing the static unseal key via `extraSecretEnvironmentVars` — OpenBao wouldn't start at all. The key needs to be mounted as a volume instead. See [docs/openbao.md](docs/openbao.md) for the full list of OpenBao gotchas.

### Longhorn must not run on control-plane nodes

This cluster runs control-plane-only nodes (no dedicated workers), so Longhorn runs on control-planes by default. On clusters with dedicated workers, Longhorn should be restricted to worker nodes. `nodeSelector` with a worker label didn't work reliably — a node affinity rule that excludes `control-plane` nodes via `DoesNotExist` is more robust.

### kube-prometheus-stack needs Pod Security Admission exemption

Prometheus components use privileged operations (node exporters etc.) which violate the default `baseline` PodSecurity policy. The namespace needs a `pod-security.kubernetes.io/enforce: privileged` label, otherwise pods get blocked silently.

### Cilium and long DNS lookups on Talos

DNS lookups were taking unusually long. Fix: `bpf.hostLegacyRouting: true` in the Cilium Helm values — makes Cilium compatible with Talos DNS routing.

### Kustomization patches to avoid repeating yourself

Every HelmRelease needed the same boilerplate: `interval`, `timeout`, `driftDetection`, `install`, `upgrade`, `test`. Instead of copying it into every file, used Kustomization-level `patches` in `clusters/infra.yml` to inject these into all HelmReleases at once. Much cleaner.

### CRDs: CreateReplace on install, Skip on upgrade

CRD handling needs different strategies depending on the operation: `crds: CreateReplace` on install (to actually apply them), `crds: Skip` on upgrade (to avoid overwriting CRDs that other controllers may depend on). Getting this wrong causes silent CRD drift.

### Drift detection needs ignore rules

Enabling `driftDetection` immediately caused false positives — Flux flagged legitimate changes as drift: `/spec/replicas` modified by HPA, and `/status` on cert-manager Certificates written back by the controller. Fix: add ignore rules for both. Also set `mode: warn` instead of `enabled` so drift is logged but doesn't block reconciliation. Both rules are now applied globally via a Kustomization patch in `clusters/infra.yml` instead of repeating them in every HelmRelease.

### Longhorn storage needs ReadWriteMany

Longhorn PVCs with `ReadWriteOnce` fail when pods get rescheduled to a different node. Use `ReadWriteMany` — Longhorn supports it and it's required for stable operation across multiple nodes.

### Cilium transparent DNS proxy causes issues

The transparent DNS proxy in Cilium interfered with DNS routing. Needs to be explicitly disabled: `dnsproxy.enableTransparentMode: false`.

### Secrets tool evolution: HashiCorp Vault → Sealed Secrets → OpenBao + External Secrets

Started with HashiCorp Vault, switched after the BSL license change. Tried Sealed Secrets in between — encrypts secrets and stores them in Git, works fine, but managing everything through a central UI outside of Git is nicer day-to-day. Landed on OpenBao (open-source Vault fork) + External Secrets.

## 📝 Docs

- [cert-manager](docs/cert-manager.md)
- [cert-manager & Gateway API](docs/cert-manager%20and%20gateway-api.md)
- [External DNS](docs/external-dns.md)
- [OpenBao](docs/openbao.md)
- [Flagger Canary Deployment](docs/flagger%20canary%20deployment%20ci-cd.md)
- [Monitoring & Observability](docs/monitoring.md)
