# make gateway api run with cert-manager

https://cert-manager.io/docs/usage/gateway/

need to set this in the helm values:

config:
  apiVersion: controller.config.cert-manager.io/v1alpha1
  kind: ControllerConfiguration
  enableGatewayAPI: true

using apiVersion: gateway.networking.k8s.io/v1 seems to be enough

and therefore installing the standard crds of gateway is also enough:

https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/standard-install.yaml
