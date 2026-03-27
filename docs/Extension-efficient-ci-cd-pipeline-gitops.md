# Production CI/CD Architecture — Complete Overview

## Core Principle

The artifact (Docker Image with SHA/Tag) stays the same across all stages — you promote the image, you don’t rebuild it. GitHub Actions does minimal work, the cluster handles almost everything itself.

```
Code Push → CI (Tests + Build) → Image Push → GitOps Commit → Flux syncs → Cluster deploys
```

-----

## Environments

|Environment|Purpose            |Deploy Trigger                   |
|-----------|-------------------|---------------------------------|
|Preview    |Per PR, short-lived|Automatic on PR                  |
|Staging    |Stable, 24/7       |Automatic on merge to `main`     |
|Prod       |The real thing     |Manual approval or tag (`v1.2.3`)|

Separate clusters per environment — not namespaces on a shared cluster. Why:

- A failure in staging doesn’t kill prod
- Separate RBAC — devs have limited/no direct access to prod
- Staging smaller = cheaper
- Blast radius contained

-----

## CI Phase: GitHub Actions

Actions does **only**:

1. Run tests (Unit, Integration, SAST/Trivy)
1. Build Docker image + push to GHCR
1. Update image tag in `kustomization.yaml` (a single `yq` one-liner)
1. Commit + push to GitOps repo

```yaml
# Example: update image tag
- name: Update image tag
  run: |
    yq e '.images[0].newTag = "${{ github.sha }}"' \
      -i apps/staging/my-app/kustomization.yaml
```

**No `kubectl apply`, no SSH to the cluster, no cluster credentials in the pipeline.** Flux pulls — Actions doesn’t push.

-----

## GitOps: Flux CD

### Repo Structure

```
gitops-repo/
├── clusters/
│   ├── staging/
│   │   ├── flux-system/
│   │   ├── apps.yaml             # → apps/staging
│   │   └── infrastructure.yaml  # → infrastructure/staging
│   └── prod/
│       ├── flux-system/
│       ├── apps.yaml             # → apps/prod
│       └── infrastructure.yaml
│
├── apps/
│   ├── base/
│   │   └── my-app/
│   │       ├── deployment.yaml
│   │       └── kustomization.yaml
│   ├── staging/
│   │   └── my-app/
│   │       └── kustomization.yaml   # Staging overrides
│   └── prod/
│       └── my-app/
│           └── kustomization.yaml   # Prod overrides
│
└── infrastructure/
    ├── base/
    ├── staging/
    └── prod/
```

Flux runs on **each cluster separately** and only watches its own folder. The clusters don’t know about each other — the GitOps repo is the single source of truth.

### Kustomize Overlays

Base holds the shared logic. Staging/prod only override what differs:

```yaml
# apps/staging/my-app/kustomization.yaml
bases:
  - ../../base/my-app
patches:
  - patch: |
      - op: replace
        path: /spec/replicas
        value: 1
      - op: replace
        path: /spec/template/spec/containers/0/resources
        value:
          requests:
            memory: "128Mi"
            cpu: "50m"
          limits:
            memory: "256Mi"
images:
  - name: my-app
    newTag: sha-abc1234    # Fresh SHA
```

```yaml
# apps/prod/my-app/kustomization.yaml
bases:
  - ../../base/my-app
patches:
  - patch: |
      - op: replace
        path: /spec/replicas
        value: 3
images:
  - name: my-app
    newTag: v1.2.3         # Tagged releases only
```

### Typical Staging vs Prod Differences

|                   |Staging                  |Prod                      |
|-------------------|-------------------------|--------------------------|
|Replicas           |1                        |3+                        |
|Image Tag          |`sha-abc1234`            |`v1.2.3`                  |
|Resources          |Small                    |Generous                  |
|Ingress            |`staging.myapp.com`      |`myapp.com` + strict TLS  |
|HPA                |Off                      |On                        |
|PodDisruptionBudget|Off                      |On                        |
|Secrets            |Vault path `/staging/...`|Vault path `/prod/...`    |
|Flagger Steps      |Aggressive (fast)        |Conservative (slow + safe)|

-----

## Deployments: Flagger

Flagger sits on the cluster, intercepts Flux deployments and controls the rollout.

### Blue/Green

Two identical environments, traffic switch in seconds:

```
Blue (live) ──► Router ◄── Green (new version)
                  │
            Switch happens
            in seconds — rollback just as fast
```

```yaml
strategy:
  blueGreen:
    activeService: app-active
    previewService: app-preview
    autoPromotionEnabled: false   # Promote manually
```

### Canary

Gradual traffic shift with automatic metric analysis:

```yaml
strategy:
  canary:
    steps:
    - setWeight: 10
    - pause: {duration: 10m}
    - setWeight: 30
    - pause: {duration: 10m}
    - setWeight: 50
    - pause: {analysis: true}     # Check metrics
    - setWeight: 100
```

### Auto-Rollback via Metrics

```yaml
analysis:
  interval: 1m
  threshold: 5           # Max 5 failed checks
  maxWeight: 50
  stepWeight: 10
  metrics:
  - name: error-rate
    thresholdRange:
      max: 1             # >1% errors → automatic rollback
  - name: latency-p99
    thresholdRange:
      max: 500           # >500ms p99 → rollback
```

**Argo Rollouts vs Flagger:**

- **Argo Rollouts** — own `Rollout` resource, more manual control, nice dashboard
- **Flagger** — works on top of existing Deployments, fully automatic, “set it and forget it”

-----

## Metrics: What to Measure

### Google’s Four Golden Signals

- **Latency** — how long requests take (including failed ones)
- **Traffic** — requests per second
- **Errors** — error rate (5xx etc.)
- **Saturation** — how full the system is (CPU, memory, queue depth)

### RED Method (for services — most relevant for Flagger)

- **R**ate — requests per second
- **E**rrors — error rate
- **D**uration — latency (p99)

### USE Method (for infrastructure/nodes)

- **U**tilization — resource usage
- **S**aturation — how full
- **E**rrors — hardware-level errors

**Important:** Thresholds are based on experience — there’s no universal truth. First observe metrics, then set thresholds, then enable auto-rollback.

-----

## Secrets: OpenBao

### Centralized Approach

OpenBao is **Secrets Manager + OIDC Provider** for everything internal. One system, does everything.

```
OpenBao
  ├── Staging secrets  (/staging/db, /staging/api-keys)
  ├── Prod secrets     (/prod/db, /prod/api-keys)
  ├── OIDC Provider    (for Kubernetes, internal apps)
  └── Auth Backend     (JWT for GitHub/GitLab Actions)
```

### ExternalSecrets Operator

Pulls secrets from OpenBao into the cluster — automatically, with rotation:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
spec:
  secretStoreRef:
    name: openbao
  target:
    name: db-credentials
  data:
  - secretKey: password
    remoteRef:
      key: staging/db        # Staging path
      property: password
```

The prod overlay only overrides the path to `prod/db`. Secret rotation is fully automatic — OpenBao rotates, ExternalSecrets detects the change, updates the K8s secret, pod restarts. No human involvement needed.

### GitHub Actions Auth against OpenBao

No static token stored in GitHub Secrets. GitHub issues a short-lived JWT, OpenBao trusts GitHub as an external OIDC provider:

```
GitHub Actions → JWT Token (short-lived, signed by GitHub)
                      ↓
              OpenBao verifies:
              - Is this really from GitHub?
              - Which repo? Which branch?
                      ↓
              Returns only the permitted secrets
```

```yaml
# GitHub Actions
- uses: hashicorp/vault-action@v2
  with:
    url: https://vault.mycompany.com
    method: jwt
    role: github-ci
    secrets: |
      secret/data/ci/docker REGISTRY_TOKEN | DOCKER_TOKEN
```

You can even restrict: only the `main` branch gets prod secrets, feature branches don’t.

**Why two OIDC providers (GitHub + OpenBao)?**  
GitHub issues its JWT in a hardcoded way — it can’t be replaced. For CI/CD pipelines you accept this. For everything else (Kubernetes, internal apps, services) OpenBao is the sole provider. The GitHub JWT is only used in this one context.

-----

## Cluster Provisioning

Talos + Terraform — declarative, reproducible, fits the GitOps philosophy:

```
terraform apply → Talos cluster is up
      ↓
flux bootstrap → Flux installs itself
      ↓
Flux syncs GitOps repo → everything else deploys automatically
```

-----

## The Complete Flow

```
1. Developer pushes code to feature branch
         ↓
2. GitHub Actions: Tests + Build + Image Push (sha-abc1234 → GHCR)
         ↓
3. Actions commits new image tag to GitOps repo (staging/)
         ↓
4. Flux on staging cluster detects Git change → syncs
         ↓
5. Flagger intercepts → runs Canary with 10/30/50/100% steps
         ↓
6. Prometheus metrics in green range → promotion
   Metrics red → automatic rollback
         ↓
7. QA/Tests on staging green → manual approval
         ↓
8. Actions commits image tag v1.2.3 to GitOps repo (prod/)
         ↓
9. Flux on prod cluster syncs → Flagger runs conservative Canary
         ↓
10. Auto-rollback or promotion — no human intervention needed
```

-----

## Why This Architecture Is Brilliant

- **GitHub Actions does minimal work** — no kubectl, no SSH, no cluster credentials
- **Flux pulls, nobody pushes** — more secure, auditable, reproducible
- **Kustomize Overlays** — one base, minimal overrides, no copy-paste
- **Flagger Auto-Rollback** — cluster decides itself, based on real metrics
- **OpenBao centralized** — one secret system, automatic rotation, no static tokens
- **Separate clusters** — staging failures don’t kill prod
- **Talos + Terraform** — cluster is code, reproducible in minutes
