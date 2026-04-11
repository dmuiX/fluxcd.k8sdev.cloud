Hier der komplette Stack den wir heute erarbeitet haben:
Prävention:

* SHA Pinning → nichts wird heimlich ausgetauscht
* Renovatebot + `minimumReleaseAge` + `osvVulnerabilityAlerts` → bekannte Angriffe auf direkte deps
* Trivy in Pipeline → komplettes Image inkl. transitive deps via OSV Feed
* Trivy + CI Tools → manuell im Blick behalten bei Updates
Assume Breach:
* Hubble → echten Traffic beobachten
* Network Policies daraus ableiten → kein lateral movement
* `automountServiceAccountToken: false` → kein K8s API Zugriff
* Non-root Container
* RBAC minimal
Wichtige Erkenntnisse:
* Supply Chain ≠ CVE — zwei verschiedene Probleme
* Renovatebot nur direkte deps → Trivy schließt die Lücke für transitive
* K8s ist by default komplett offen intern — Namespaces isolieren nicht
* Cilium by default auch offen — `policyEnforcementMode=always` für Zero Trust
* Gegen unbekannte Angriffe kannste dich nicht schützen → Blast Radius minimieren
Das Kernprinzip: Defense in Depth — keine einzelne Maßnahme reicht, aber zusammen macht's lateral movement extrem schwer. 💪

Kannst du das ins global memory speichern
