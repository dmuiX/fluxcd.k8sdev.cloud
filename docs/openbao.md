# openbao

## neet to define a static-key beforehand

static_key=$(openssl rand -base64 32 | tee /dev/tty)

kubectl create secret generic unseal-keys -n openbao --from-literal=static-key="${static_-key}" --dry-run=client -o yaml | kubectl apply -f -

## need to run that script to initialize the raft backend

script doesnt work very well here the manual approach:

```bash
# 0. Check if already initialized
kubectl exec -n openbao openbao-0 -- bao status

# 1. If not initialized, initialize and save keys
kubectl exec -n openbao openbao-0 -- bao operator init | tee openbao-keys.txt
chmod 600 openbao-keys.txt
echo "Keys saved to openbao-keys.txt"

# 2. Display the keys
cat openbao-keys.txt

# 3. Unseal leader with first 3 keys
grep "Unseal Key" openbao-keys.txt | head -3 | awk '{print $NF}' | while read key; do kubectl exec -n openbao openbao-0 -- bao operator unseal "$key"; done

# 4. Verify leader is unsealed
kubectl exec -n openbao openbao-0 -- bao status

# 5. Join second node
kubectl exec -n openbao openbao-1 -- bao operator raft join http://openbao-0.openbao-internal:8200

# 6. Wait a few seconds
sleep 5

# 7. Unseal second node (same 3 keys)
grep "Unseal Key" openbao-keys.txt | head -3 | awk '{print $NF}' | while read key; do kubectl exec -n openbao openbao-1 -- bao operator unseal "$key"; done

# 8. Verify both unsealed
kubectl exec -n openbao openbao-0 -- bao status
kubectl exec -n openbao openbao-1 -- bao status

# 9. Check cluster peers
kubectl exec -n openbao openbao-0 -- bao operator raft list-peers

# 10. Extract root token for login
echo "Root Token:"
grep "Initial Root Token" openbao-keys.txt | awk '{print $NF}'
```


## setup a secretstore

1. then login into the ui with the root token
2. # Enable KV v2 at the path "secret"
kubectl exec -n openbao openbao-0 -- bao secrets enable -path=secret kv-v2
3. 