# external-dns

to bootstrap it create the pihole secret manually!

 k create secret generic pihole -n external-dns --from-literal=EXTERNAL_DNS_PIHOLE_PASSWORD="" --from-literal=EXTERNAL_DNS_PIHOLE_API_VERSION="" --from-literal=EXTERNAL_DNS_PIHOLE_SERVER="" --dry-run=client -o yaml | k apply -f -