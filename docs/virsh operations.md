# change memories of vms to 7GiB

a bit of a dumb idea to change haproxy as well but okay
```
for vm in control-node-1 control-node-2 control-node-3 haproxy; do echo "=== $vm ==="; virsh setmem $vm 7GiB --config; done
```
changed it back
```
virsh setmem haproxy 768MiB --config
```

 for vm in control-node-1 control-node-2 control-node-3 haproxy; do echo "=== $vm ==="; virsh dominfo $vm; done

 thats the command to get info

 After chagnging starting my machines

for vm in control-node-1 control-node-2 control-node-3 haproxy; do echo "=== $vm ==="; virsh start $vm; done

and then check nodes and pods


virsh list --all
k get nodes -w
k get nodes
k get pods -A

