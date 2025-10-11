#!/bin/bash

NAMESPACE="openbao"

echo "Initializing OpenBao Raft cluster on openbao-0..."
kubectl exec -n $NAMESPACE openbao-0 -- bao operator init

echo "Unsealing openbao-0..."
kubectl exec -n $NAMESPACE openbao-0 -- bao operator unseal

echo "Joining openbao-1 to Raft cluster..."
kubectl exec -n $NAMESPACE openbao-1 -- bao operator raft join http://openbao-0.$\{NAMESPACE\}-internal:8200

echo "Unsealing openbao-1..."
kubectl exec -n $NAMESPACE openbao-1 -- bao operator unseal

echo "OpenBao Raft cluster initialization and unseal complete."
