# How to restore

create an restore object

might already exists inside the repo

then create these secrets

```bashgit 
namespace=monitoring
 k8up_repo_password=
k create secret generic k8up-repo-password -n $namespace --from-literal=password=$k8up_repo_password

 AWS_ACCESS_KEY_ID=
 AWS_SECRET_ACCESS_KEY=

k create secret generic r2-credentials -n $namespace --from-literal=access-key-id=$AWS_ACCESS_KEY_ID --from-literal=secret-acces-key=$AWS_SECRET_ACCESS_KEY
```
