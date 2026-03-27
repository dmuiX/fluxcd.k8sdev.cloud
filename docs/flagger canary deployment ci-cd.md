# Flagger Setup mit Cilium + Flux — Debug Notes

## Stack

- Kubernetes: Talos
- GitOps: Flux CD
- Gateway: Cilium (Gateway API / HTTPRoutes)
- Metrics: kube-prometheus-stack
- Progressive Delivery: Flagger + Loadtester

---

## Problem 1: meshProvider nicht gesetzt → Istio Fehler

**Fehler:**
```
reconcileDestinationRule failed: DestinationRule podinfo-canary.podinfo create error:
the server could not find the requested resource (post destinationrules.networking.istio.io)
```

**Ursache:** Flagger fällt auf Istio zurück wenn `meshProvider` nicht explizit gesetzt ist.

**Fix:** In Flagger HelmRelease values:
```yaml
values:
  meshProvider: gatewayapi
  prometheus.install: false
  metricsServer: http://monitoring-prometheus.monitoring:9090
```

---

## Problem 2: gatewayRefs ohne group/kind → Istio Fallback

**Fehler:** Gleicher Istio Fehler wie oben obwohl meshProvider korrekt gesetzt.

**Ursache:** In der Canary Resource fehlten `group` und `kind` in `gatewayRefs`. Ohne diese fällt Flagger auf Istio zurück.

**Fix:**
```yaml
service:
  gatewayRefs:
    - name: podinfo
      namespace: podinfo
      group: gateway.networking.k8s.io   # wichtig!
      kind: Gateway                       # wichtig!
```

---

## Problem 3: MetricTemplate nicht gefunden

**Fehler:**
```
metric template error-rate.flagger error: metrictemplate.flagger.app "error-rate" not found
```

**Ursache:** Die Canary Resource referenziert MetricTemplates die noch nicht im Cluster existieren.

**Fix:** MetricTemplates zuerst deployen, bevor die Canary Resource applied wird. In Flux mit `dependsOn` sicherstellen:
```yaml
dependsOn:
  - name: flagger-metric-templates
```

---

## Problem 4: HPA nicht gefunden

**Fehler:**
```
HorizontalPodAutoscaler podinfo.podinfo get query error: horizontalpodautoscalers.autoscaling "podinfo" not found
```

**Ursache:** Canary referenziert einen HPA der nicht existiert.

**Fix:** HPA erstellen — Name muss exakt dem Namen in der Canary Resource entsprechen:
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: podinfo       # muss mit autoscalerRef.name übereinstimmen
  namespace: podinfo
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: podinfo
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 50
```

---

## Problem 5: Prometheus URL falsch

**Fehler:**
```
running query failed: Get "http://prometheus.monitoring:9090/api/v1/query": dial tcp:
lookup prometheus.monitoring: no such host
```

**Ursache:** Flagger Default Prometheus URL passt nicht zum tatsächlichen Service-Namen von kube-prometheus-stack.

**Diagnosis:**
```bash
kubectl get svc -A | grep prometheus
```

Ergab: Service heißt `monitoring-prometheus` im Namespace `monitoring`.

**Fix:** In Flagger HelmRelease:
```yaml
values:
  metricsServer: http://monitoring-prometheus.monitoring:9090
```

---

## Problem 6: kube-prometheus-stack CRDs veraltet

**Fehler:**
```
Alertmanager/monitoring/monitoring-alertmanager dry-run failed:
.spec.hostNetwork: field not declared in schema
```

**Ursache:** CRDs im Cluster sind älter als die Chart-Version. Helm updated CRDs by default nicht automatisch.

**Fix:** CRDs manuell ins GitOps Repo packen und als eigene Kustomization deployen (vor dem HelmRelease via `dependsOn`).

Passende CRD Version zur Chart ermitteln:
```bash
helm show chart kube-prometheus-stack \
  --repo https://prometheus-community.github.io/helm-charts \
  --version 82.15.0 | grep appVersion
# → v0.89.0
```

CRDs von passender Tag-Version holen:
```
https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/v0.89.0/bundle.yaml
```

---

## Problem 7: Flagger cached alten State nach Config-Änderung

**Symptom:** Fehler bleibt bestehen obwohl Canary Resource bereits korrigiert wurde.

**Fix:** Canary löschen und Flux neu syncen:
```bash
kubectl delete canary podinfo -n podinfo
flux reconcile kustomization apps --with-source
```

Oder Flagger neu starten:
```bash
kubectl rollout restart deploy/flagger -n flux-system
```

---

## Problem 8: Flux HelmRelease hängt / verklemmt

**Symptom:** HelmRelease bleibt auf `Failed` oder `Progressing` hängen.

**Fix:**
```bash
flux suspend helmrelease <name> -n <namespace>
flux resume helmrelease <name> -n <namespace>
```

Oder kompletter Resync:
```bash
flux reconcile source git flux-system
flux reconcile kustomization flux-system --with-source
```

---

## Problem 9: dependsOn Namespace falsch

**Fehler:**
```
unable to get 'flagger/flagger' dependency: helmreleases.helm.toolkit.fluxcd.io "flagger" not found
```

**Ursache:** `dependsOn` sucht immer im gleichen Namespace wie die Resource selbst — nicht in `flux-system`.

**Fix:** Namespace explizit angeben:
```yaml
dependsOn:
  - name: flagger
    namespace: flux-system
```

---

## Problem 10: Flagger Loadtester Helm Chart schwer zu finden

**Symptom:** Flagger Doku zeigt nur OCI-basierten Install für den Loadtester — kein Helm Chart erwähnt.

**Lösung:** Helm Chart existiert, ist aber nur über das Flagger Helm Repo verfügbar — nicht über OCI:

```bash
helm repo add flagger https://flagger.app
helm upgrade -i flagger-loadtester flagger/loadtester
```

Als HelmRelease:
```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: flagger-loadtester
  namespace: flux-system
spec:
  releaseName: flagger-loadtester
  targetNamespace: flagger
  chart:
    spec:
      chart: loadtester
      version: 0.36.0
      sourceRef:
        kind: HelmRepository
        name: flagger    # gleiches HelmRepository wie Flagger selbst
  values:
    replicaCount: 1
    podDisruptionBudget:
      enabled: false
  dependsOn:
    - name: flagger
      namespace: flux-system
```

**Versions-Chaos:** Flagger selbst und der Loadtester haben separate Versionierung — laufen nicht synchron. Flagger `1.42.0`, Loadtester `0.36.0`. Aktuelle Version am schnellsten auf ArtifactHub finden:
```
https://artifacthub.io/packages/helm/flagger/loadtester
```

---

## Problem 11: podDisruptionBudget Dot-Notation funktioniert nicht in HelmRelease

**Falsch:**
```yaml
values:
  podDisruptionBudget.enabled: true
```

**Richtig:**
```yaml
values:
  podDisruptionBudget:
    enabled: true
```

Dot-Notation geht nur in Helm CLI mit `--set`, nicht im HelmRelease `values` Block in YAML.

---

## Problem 12: Prometheus Service Name ermitteln

kube-prometheus-stack benennt den Prometheus Service nicht einfach `prometheus` sondern abhängig vom Release-Namen. Immer zuerst nachschauen:

```bash
kubectl get svc -A | grep prometheus
```

Dann den ClusterIP Service nehmen (nicht den Pod-Namen, nicht `prometheus-operated`):
```
monitoring-prometheus.monitoring:9090
```

Format: `<service-name>.<namespace>:<port>`

---

## Lessons Learned

- **`kubectl apply --dry-run=server -f file.yaml`** vor jedem Apply — validiert gegen echte API, erkennt fehlende CRDs und falsche Felder sofort
- **`kubeconform`** als Pre-commit Hook im GitOps Repo verhindert invalides YAML vor dem Push
- **`flux suspend/resume`** ist der schnellste Fix wenn ein HelmRelease feiert
- **`kubectl rollout restart`** auf Flagger wenn Canary-State cached ist
- **MetricTemplates immer zuerst deployen** bevor Canary Resource erstellt wird
- **CRDs versioniert ins Repo** — nie auf Helm verlassen für CRD Updates
- **Immer `group` und `kind` in `gatewayRefs`** angeben — sonst Istio Fallback