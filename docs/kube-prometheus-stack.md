## upgrade crds manually

With Helm v3, CRDs created by this chart are not updated by default and should be manually updated. Consult also the Helm Documentation on CRDs.

CRDs update lead to a major version bump. The Chart's appVersion refers to the prometheus-operator's version with matching CRDs.

See helm upgrade for command documentation.

ergo

curl -s "<https://api.github.com/repos/prometheus-community/helm-charts/contents/charts/kube-prometheus-stack/charts/crds/crds>" \
  | grep '"download_url"' \
  | cut -d'"' -f4 \
  | while read url; do curl -sL "$url"; echo "---"; done \
  > infra/crds/kube-prometheus-stack.yaml

# Monitoring & Observability Setup

## Stack

| Component | Role | Config |
|---|---|---|
| kube-prometheus-stack | Prometheus + Grafana + Alertmanager | `infra/controller/kube-prometheus-stack.yml` |
| Loki | Log storage (SingleBinary, Longhorn) | `infra/controller/loki.yml` |
| Grafana Alloy | Log collector (DaemonSet) | `infra/controller/grafana-alloy.yml` |
| Hubble | L7 HTTP metrics via eBPF | Enabled in `infra/controller/cilium.yml` |
| Flagger | Canary analysis via Hubble metrics | `infra/controller/flagger.yml` |

---

## Prometheus: ServiceMonitor Discovery

kube-prometheus-stack injiziert per Default `release: kube-prometheus-stack` als Label-Selector auf die Prometheus CR. Damit werden nur ServiceMonitors mit diesem Label gescrapt.

**Problem:** Der Default-Value `serviceMonitorSelectorNilUsesHelmValues` ist `true`. Das bedeutet: selbst wenn man `serviceMonitorSelector: {}` setzt, ueberschreibt das Chart es mit `release: <releaseName>`. Um wirklich alle ServiceMonitors cluster-weit zu matchen:

```yaml
prometheus:
  prometheusSpec:
    serviceMonitorSelector: {}
    serviceMonitorSelectorNilUsesHelmValues: false
```

**Hintergrund:** `serviceMonitorSelector:` ohne Wert ist YAML `nil` — das Chart interpretiert das als "nicht gesetzt" und faellt auf den Helm-Release-Label zurueck. `serviceMonitorSelector: {}` ist ein leeres Objekt und bedeutet "matche alles". Aber nur in Kombination mit `serviceMonitorSelectorNilUsesHelmValues: false` wird der Selector auch wirklich leer uebernommen.

Die Steuerung, welche Services gemonitort werden, erfolgt ueber `serviceMonitor.enabled: true/false` in den jeweiligen Helm-Chart-Values — nicht ueber den Prometheus-Selector. Wenn ein Chart keinen ServiceMonitor erstellt, gibt es nichts zu discovern.

**Verifizieren:**

```bash
# Prometheus CR pruefen — matchLabels sollte LEER sein
kubectl get prometheus -n monitoring -o yaml | grep -A 5 "serviceMonitor"

# Alle ServiceMonitors im Cluster
kubectl get servicemonitor -A

# Targets checken
kubectl port-forward -n monitoring svc/monitoring-prometheus 9090:9090
# → http://localhost:9090/targets
# Query: up
```

---

## Grafana Dashboard Datasource Mapping

Community-Dashboards von grafana.com nutzen `__inputs`-Variablen fuer Datasources (z.B. `DS_PROMETHEUS`, `DS_METRICS`). Der Variablenname ist pro Dashboard unterschiedlich. In den kube-prometheus-stack Values muss das Mapping als Liste angegeben werden:

```yaml
grafana:
  dashboards:
    grafana-dashboards:
      cert-manager: # https://grafana.com/grafana/dashboards/20842
        gnetId: 20842
        revision: 3
        datasource:
          - name: DS_PROMETHEUS
            value: Prometheus
      external-secrets: # https://grafana.com/grafana/dashboards/21640
        gnetId: 21640
        revision: 4
        datasource:
          - name: DS_METRICS
            value: Prometheus
```

**Falsch** (funktioniert nicht bei allen Dashboards):

```yaml
datasource: Prometheus
```

**Richtig:**

```yaml
datasource:
  - name: DS_PROMETHEUS
    value: Prometheus
```

Den korrekten Variablennamen findet man im Dashboard-JSON unter `__inputs` (auf grafana.com → Download JSON).

---

## Bekannte Fallstricke

### `serviceName` in `prometheusSpec`/`alertmanagerSpec` nicht setzen

`serviceName` ist ein Feld der Prometheus/Alertmanager-CRD, das vom Operator verwaltet wird. Wenn es ueber Helm-Values gesetzt wird, kaempfen Helm und der Prometheus-Operator gegeneinander — das fuehrt zu perpetuellem `DriftDetected` (1 addition) auf der Alertmanager-Ressource.

### Upgrade-Timeout

kube-prometheus-stack benoetigt bei Erstinstallation oder nach groesseren Aenderungen mehr als 5 Minuten. Timeout auf mindestens `15m0s` setzen:

```yaml
spec:
  timeout: 15m0s
```

### Grafana Datasource Provisioning Fehler

```
Datasource provisioning error: unique identifier and org id are needed
```

Tritt auf wenn eine Datasource-ConfigMap eine doppelte oder fehlende UID hat. Haeufig durch Tippfehler im YAML (z.B. `-name` statt `- name` in der Datasource-Liste). Check:

```bash
kubectl get configmap -n monitoring -l grafana_datasource=1 -o yaml
```

### Loki Helm Repository (Stand Maerz 2026)

Grafana hat den Loki-Chart am 16. Maerz 2026 in die Community geforkt. Neue URL:

```yaml
# loki.yml HelmRepository
url: https://grafana-community.github.io/helm-charts
```

Die alte URL `https://grafana.github.io/helm-charts` ist fuer Loki deprecated (nur noch GEL/Enterprise).

---

## Hubble HTTP Metrics aktivieren

Hubble UI und Relay allein exportieren **keine** Prometheus-Metriken. Die muessen explizit aktiviert werden.

**In Cilium Helm Values:**

```yaml
hubble:
  metrics:
    enableOpenMetrics: true
    enabled:
      - dns
      - drop
      - tcp
      - flow
      - icmp
      - "httpV2:exemplars=true;labelsContext=source_ip,source_namespace,source_workload,destination_ip,destination_namespace,destination_workload,traffic_direction"
    serviceMonitor:
      enabled: true
```

`httpV2` mit `labelsContext` ist entscheidend — ohne die Workload-Labels kann Flagger nicht pro Canary filtern.

**Exportierte Metriken:**

- `hubble_http_requests_total` — Request Counter mit `response_code`, `destination_workload`, etc.
- `hubble_http_request_duration_seconds_bucket` — Latency Histogram

**Verifizieren:**

```bash
# Direkt vom Cilium Agent Pod
kubectl run -it --rm debug --image=curlimages/curl --restart=Never -- \
  curl -s http://<cilium-agent-pod-ip>:9965/metrics | grep hubble_http | head -5
```

---

## Warum Hubble und nicht Envoy Metriken?

Bei Cilium laeuft Envoy als **shared DaemonSet** pro Node, nicht als Sidecar pro Pod wie bei Istio. Envoy-Metriken sind daher pro Node aggregiert — keine Workload-Granularitaet.

Hubble sitzt im eBPF-Layer und sieht jeden einzelnen Flow inkl. Source/Destination Workload. Mit `httpV2` parst es die HTTP-Layer und exportiert Metriken mit Workload-Labels.

```
Istio:   Pod → Envoy-Sidecar → istio_requests_total (pro Workload)
Cilium:  Pod → Shared Envoy  → envoy_* (pro Node, nicht Workload)
         Pod → eBPF/Hubble   → hubble_http_* (pro Workload)
```

---

## Cilium + Hubble Grafana Dashboards

Drei Quellen fuer Dashboards:

### 1. Cilium Helm Chart (automatisch)

```yaml
# cilium.yml values
dashboards:
  enabled: true
  namespace: monitoring
  annotations:
    grafana_folder: Cilium

operator:
  dashboards:
    enabled: true

hubble:
  metrics:
    dashboards:
      enabled: true
      namespace: monitoring
      annotations:
        grafana_folder: Cilium
```

Erstellt automatisch ConfigMaps mit `grafana_dashboard: "1"` Label. Grafana Sidecar laedt sie.

Enthaltene Dashboards:

- **Cilium Dashboard** — Agent Internals (BPF, Conntrack, API, Memory)
- **Cilium Operator Dashboard** — Operator Reconciliation, CRD Processing
- **Hubble Dashboard** — Flows, Drops
- **Hubble L7 HTTP Metrics by Workload** — Request Rate, Error Rate, Latency by Source/Destination
- **Hubble DNS Namespace** — DNS Queries per Namespace
- **Hubble Network Overview Namespace** — Network Overview

### 2. Custom Flagger Canary Dashboard

`infra/config/grafana-dashboard-hubble-http.yml` — zeigt exakt die Metriken die Flagger fuer Canary-Analyse nutzt:

- Request Rate pro Workload + Response Code
- Error Rate (5xx) mit Threshold bei 1% (Flagger Limit)
- Latency P99 mit Threshold bei 0.5s (Flagger Limit)
- Latency P50/P95/P99 Vergleich
- Response Code Verteilung (Pie Chart)
- Request Rate by Source

### 3. Offizielles Hubble L7 HTTP Dashboard

`infra/config/grafana-dashboard-hubble-l7-http-by-workload.yml` — das offizielle Dashboard aus dem Cilium Repo mit detaillierten HTTP-Metriken nach Source/Destination inkl. CPU Usage.

---

## Loki + Alloy Setup

### Loki (Log Storage)

SingleBinary Mode mit Filesystem Storage auf Longhorn. Kein S3, kein Redis, kein HA — reicht fuer kleine Cluster.

```yaml
deploymentMode: SingleBinary
loki:
  auth_enabled: false
  commonConfig:
    replication_factor: 1
  schemaConfig:
    configs:
      - from: "2024-01-01"
        store: tsdb
        object_store: filesystem
        schema: v13
        index:
          prefix: index_
          period: 24h
  storage:
    type: filesystem
singleBinary:
  replicas: 1
  persistence:
    enabled: true
    storageClass: longhorn
    size: 10Gi
```

### Alloy (Log Collector)

DaemonSet der Logs von allen Pods sammelt und an Loki schickt. Config in Alloy's eigener River-Syntax:

```
discovery.kubernetes "pods" { role = "pod" }
→ discovery.relabel "pods" (namespace, pod, container, node labels)
→ loki.source.kubernetes "pods"
→ loki.write "default" (http://loki.monitoring:3100)
```

### Grafana Datasource

In kube-prometheus-stack Helm Values:

```yaml
grafana:
  additionalDataSources:
    - name: Loki
      type: loki
      url: http://loki.monitoring:3100
      access: proxy
      isDefault: false
```

---

## Flagger MetricTemplates

Die MetricTemplates muessen zur Metriken-Quelle passen. Mit Cilium/Hubble:

```yaml
# Error Rate
query: |
  100 - sum(rate(hubble_http_requests_total{
    destination_workload_namespace=~"{{ namespace }}",
    destination_workload=~"{{ target }}",
    response_code!~"5.*",
  }[{{ interval }}])) /
  sum(rate(hubble_http_requests_total{
    destination_workload_namespace=~"{{ namespace }}",
    destination_workload=~"{{ target }}",
  }[{{ interval }}])) * 100

# Latency P99
query: |
  histogram_quantile(0.99, sum(rate(
    hubble_http_request_duration_seconds_bucket{
      destination_workload_namespace=~"{{ namespace }}",
      destination_workload=~"{{ target }}",
    }[{{ interval }}]
  )) by (le))
```

**Nicht** `istio_requests_total` — die existieren nur mit Istio.

---

## Troubleshooting

### Dashboards zeigen keine Daten

1. **Prometheus Targets checken** — `http://localhost:9090/targets` nach Port-Forward
2. **`up` Query** — zeigt alle gescrapten Targets. Wenn nur `monitoring` und `kube-system` Namespaces auftauchen, greift noch der alte Label-Selector
3. **ServiceMonitors vorhanden?** — `kubectl get servicemonitor -A`
4. **Metriken exportiert?** — `curl http://<pod-ip>:<port>/metrics | grep <metric>`
5. **Label-Selector Problem?** — `kubectl get prometheus -n monitoring -o yaml | grep -A 5 serviceMonitor` — `matchLabels` sollte leer sein
6. **Dashboard Datasource Variable?** — Fehler wie `DS_METRICS not found` oder `DS_PROMETHEUS not found` bedeuten falsches Datasource-Mapping in den Dashboard-Values (siehe oben)
7. **Pods neu gestartet nach Config-Aenderung?** — `kubectl rollout restart daemonset/cilium -n cilium`

### Flagger DestinationRule Fehler

```
reconcileDestinationRule failed: DestinationRule podinfo-canary.podinfo create error
```

Flagger faellt auf Istio zurueck. Checkliste:

- `meshProvider: "gatewayapi:v1"` in Flagger Helm Values
- `provider: "gatewayapi:v1"` in Canary Resource
- `gatewayRefs` mit `group: gateway.networking.k8s.io` und `kind: Gateway`
- Canary loeschen und neu erstellen falls gecached: `kubectl delete canary <name> -n <ns>`
