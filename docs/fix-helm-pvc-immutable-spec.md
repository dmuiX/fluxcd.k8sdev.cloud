# Fix: Helm overwrites bound PVC on upgrade

## Problem

When upgrading opencost via Helm (replica count set to 3), Helm tried to replace the existing `PersistentVolumeClaim` instead of patching it. This cleared the `VolumeName` — Kubernetes rejects this because PVC specs are immutable after binding.

```
failed to replace object: PersistentVolumeClaim "opencost-pvc" is invalid:
spec: Forbidden: spec is immutable after creation
```

Root cause: `force: true` in the global HelmRelease patch triggers delete + recreate instead of patch on conflicts. For PVCs this means: `VolumeName` gets wiped.

## Root Cause Analysis

1. Global kustomize patch sets `upgrade.force: true` on all HelmReleases
2. Helm tries to replace PVC → `VolumeName` gets cleared
3. Kubernetes: "spec is immutable" → upgrade fails
4. Flux rolls back → annotation from previous `kubectl annotate` is gone
5. Next upgrade attempt → same error

## Fix

### 1. Remove `force: true` from global patch

`force: true` makes sense for Deployments and ConfigMaps, but not for PVCs. Since PVCs are the only resources where delete+recreate risks data loss, it's safer to set `force: true` only where it's actually needed.

### 2. `helm.sh/resource-policy: keep` via postRenderer

Helm should leave the PVC completely alone after the initial install. The annotation is applied via postRenderer — so it's also set on the very first deploy.

```yaml
# HelmRelease opencost
postRenderers:
  - kustomize:
      patches:
        - patch: |
            - op: add
              path: /metadata/annotations/helm.sh~1resource-policy
              value: keep
          target:
            kind: PersistentVolumeClaim
            name: opencost-pvc
            version: v1
```

> **Note:** `/` in JSON Patch paths must be escaped as `~1`.

## Behavior

| When | What happens |
|------|-------------|
| First install | Helm creates PVC normally, annotation is applied via postRenderer |
| Every subsequent upgrade | Helm sees `keep` → skips PVC entirely |
| Rollback | postRenderer applies → annotation is preserved |

---

## Side Issue: k8up CRD Kustomization

Flux tried to apply a `kustomize.config.k8s.io/v1beta1 Kustomization` as a Kubernetes manifest. Kustomize config files must be named `kustomization.yaml` — any other filename is interpreted by Flux as a regular manifest.

**Fix:** Commit the CRD YAML directly into the repo instead of referencing it via a Kustomization.

```bash
curl -L https://github.com/k8up-io/k8up/releases/download/k8up-4.8.7/k8up-crd.yaml \
  -o infra/crds/k8up-crds.yaml
```

On updates, just re-download the file and commit — same effort as updating the URL in a Kustomization.