its pretty annoying to go through the helm fluxcd cycle when I test a new helm release and values

maybe a better way is to us k apply then I have the ressource freaking fast there

further more the problem of writing typos which happens all the time! really all the time and not finding it until I have deployed it on the cluster is soo dumb

- one way to prevent this is using cmd + shift + F to find and replace repo wide
- best way would be to have a intellisense that understands what resource I already have in the cluste and which name they have
not sure if there is anything out there for vscode? or for any other IDE?

- I use already the k8s extension but this is only for crds not for the values itselfe...
