#!/bin/sh
# Certbot supplies the certificate lineage on issuance and every successful renewal.
set -eu
: "${RENEWED_LINEAGE:?Set RENEWED_LINEAGE to the certificate directory}"
cd "$(dirname "$0")/.."

install -d -o root -g 65534 -m 0750 certs
if cmp -s "$RENEWED_LINEAGE/fullchain.pem" certs/fullchain.pem &&
   cmp -s "$RENEWED_LINEAGE/privkey.pem" certs/privkey.pem &&
   [ "$(stat -c '%u:%g:%a' certs/fullchain.pem)" = '0:65534:640' ] &&
   [ "$(stat -c '%u:%g:%a' certs/privkey.pem)" = '0:65534:640' ]; then
    exit 0
fi
staging=$(mktemp -d certs/.install.XXXXXXXX)
trap 'rm -rf "$staging"' EXIT
for file in fullchain.pem privkey.pem; do
    install -o root -g 65534 -m 0640 "$RENEWED_LINEAGE/$file" "$staging/$file"
done
mv -f "$staging/fullchain.pem" certs/fullchain.pem
mv -f "$staging/privkey.pem" certs/privkey.pem

running=$(docker compose ps --status running --quiet relay)
if [ -n "$running" ]; then
    docker compose restart relay
fi
