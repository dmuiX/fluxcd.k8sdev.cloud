# Restore with k8up

## How to restore

create an restore object

might already exists inside the repo

then create these secrets

```bash
 k8up_repo_password=
 AWS_ACCESS_KEY_ID=
 AWS_SECRET_ACCESS_KEY=
for namespace in openbao monitoring opencost; do 
  k create secret generic k8up-repo-password -n $namespace --from-literal=password=$k8up_repo_password --dry-run=client -o yaml | kubectl apply -f -
  k create secret generic r2-credentials -n $namespace --from-literal=access-key-id=$AWS_ACCESS_KEY_ID --from-literal=secret-access-key=$AWS_SECRET_ACCESS_KEY --dry-run=client -o yaml | kubectl apply -f -;
done
```

maybe not

we still need to provide the correct path to get it right...
as the bucket contains each pvc.
and we first must remove that stuff that is already inside the volume

or add a new one unsure

## check inside pod

```bash
# Pod ohne -it starten, sleep als Entrypoint
kubectl apply -n openbao -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: cleaner
spec:
  restartPolicy: Never
  containers:
  - name: cleaner
    image: busybox
    command: ["sleep", "3600"]
    volumeMounts:
    - name: data
      mountPath: /data
  volumes:
  - name: data
    persistentVolumeClaim:
      claimName: data-openbao-0
EOF

# Warten bis Running
kubectl wait --for=condition=Ready pod/cleaner -n openbao --timeout=120s

# Rein
kubectl exec -it -n openbao cleaner -- sh
# drin:  ls -la /data  &&  rm -rf /data/* /data/.[!.]*  &&  exit

# Aufräumen
kubectl delete pod cleaner -n openbao
```

## restore with restic or acces snapshots

have a look at pw safe under "k8sdev-backups r2 bucket"

 export RESTIC_REPOSITORY=s3:https://s3address/bucket
 export RESTIC_PASSWORD=
 export AWS_ACCESS_KEY_ID=
 export AWS_SECRET_ACCESS_KEY=

restic snapshots will show the snapshots