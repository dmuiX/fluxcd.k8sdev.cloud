# fluxcd.k8sdev.cloud

This repo deploys automatically apps with fluxcd.
They are deployed in this order:
1. cert-manager
2. cilium
3. longhorn
4. openbao
5. external-secrets
6. kube-prometheus-stack
7. opencost
   
external-dns is out of the order of the rest.
