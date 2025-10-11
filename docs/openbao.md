# openbao

## neet to define a static-key beforehand

static_key=$(openssl rand -base64 32 | tee /dev/tty)

kubectl create secret generic unseal-keys -n openbao --from-literal=key="${static_key}" --dry-run=client -o yaml | kubectl apply -f -

## need to run that script to initialize the raft backend

```bash
cat <<EOF > init-raft-backend.sh
#!/bin/bash

NAMESPACE="openbao"

echo "Initializing OpenBao Raft cluster on openbao-0..."
kubectl exec -n \$NAMESPACE openbao-0 -- bao operator init

echo "Unsealing openbao-0..."
kubectl exec -n \$NAMESPACE openbao-0 -- bao operator unseal

echo "Joining openbao-1 to Raft cluster..."
kubectl exec -n \$NAMESPACE openbao-1 -- bao operator raft join http://openbao-0.\${NAMESPACE}-internal:8200

echo "Unsealing openbao-1..."
kubectl exec -n \$NAMESPACE openbao-1 -- bao operator unseal

echo "OpenBao Raft cluster initialization and unseal complete."
EOF

chmod +x init-raft-backend.sh
```