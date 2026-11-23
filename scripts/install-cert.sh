#!/bin/sh
# Install the current certificate; --register also sets up future Certbot renewals.
set -eu
: "${RENEWED_LINEAGE:?Set RENEWED_LINEAGE to the certificate directory}"
cd -P -- "$(dirname "$0")/.."
install -d -o root -g 65534 -m 0750 certs
certs=$(realpath -- certs)
identity=$(stat -c '%d:%i:%u:%a' "$certs")

if [ "${1:-}" = --register ]; then
    hooks=/etc/letsencrypt/renewal-hooks/deploy
    install -d -m 0755 "$hooks"
    tmp=$(mktemp "$hooks/.vibedesk-relay.XXXXXXXX")
    trap 'rm -f "$tmp"' EXIT
    # Certbot runs as root. Store the hook itself in its trusted directory.
    {
        printf '#!/bin/sh\n'
        python3 -c 'import shlex, sys; print("set --", shlex.join(sys.argv[1:]))' "$RENEWED_LINEAGE" "$certs" "$identity"
        cat scripts/renew-cert.sh
    } > "$tmp"
    chmod 0755 "$tmp"
    mv -f "$tmp" "$hooks/vibedesk-relay"
fi
exec sh scripts/renew-cert.sh "$RENEWED_LINEAGE" "$certs" "$identity"
