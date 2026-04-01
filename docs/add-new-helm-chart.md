copy paste from old one

in this case cert-manager

change url
change version
change values
check either a github or artifact hub for values

in this case

crds must be installed separately

https://github.com/k8up-io/k8up/tree/master/charts/k8up


and it seems that here

CRDs need to be installed separately, they are no longer included in this chart.


When Flux tries to apply a set of manifests, it needs CRDs to exist before it can create instances of those custom resources. If a Kustomization contains both a CRD and a CR that uses it, the CR will fail to apply because the CRD has not been registered yet.

unsure how its possible to make that as iac...?

https://oneuptime.com/blog/post/2026-03-06-organize-crds-installation-flux-cd-repository/view

pretty straight forward
use a another custumization layer called crds where infra dependson

and then download the crds to the repo

kustomization is not so nice bc you have to name id kustomization

let it start

check what is necessary as new ressources

eg.
k8up config

new gateway/http route for uis

add them to config


in this case backup

seems like the only supported storage is S3

so I will use R2 from cloudflare as its free and s3 compatible

secrets are going to be stored inside openbao
and there fore we need two extsecrets


funny enough the pvc of openbao will also be backuped by k8up :D

you are finish!
