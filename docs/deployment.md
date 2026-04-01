with a deployment when helm creates pvcs you must set

```markdown
  postRenderers:
    - kustomize:
        patches:
          - target:
              version: v1
              kind: PersistentVolumeClaim
              name: opencost-pvc
            patch: |
              - op: add
                path: /metadata/annotations/helm.sh~1resource-policy
                value: keep
```

weird you must escape the / and use ~1...never heard of that...