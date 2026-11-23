#!/bin/sh
# Exercise the service in a disposable Linux container, with no published ports.
set -eu
cd "$(dirname "$0")/.."
runtime=${CONTAINER_CLI:-docker}
image=vibedesk-relay:smoke
name=vibedesk-relay-smoke-$$
temp=$(mktemp -d)
started=false
cleanup() {
    if [ "$started" = true ]; then
        "$runtime" stop "$name" >/dev/null || true
        "$runtime" rm "$name" >/dev/null || true
    fi
    rm -rf "$temp"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

mkdir "$temp/certs"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj '/CN=localhost' \
    -addext 'subjectAltName=DNS:localhost' -keyout "$temp/certs/privkey.pem" \
    -out "$temp/certs/fullchain.pem" >/dev/null 2>&1
# The temp parent is private. Only the disposable container gets this directory mounted.
chmod 0755 "$temp/certs"
chmod 0644 "$temp/certs/"*.pem
"$runtime" build -t "$image" .
"$runtime" run -d --name "$name" -e RELAY_DOMAIN=localhost -e RELAY_LOCAL_TEST=1 \
    -v "$temp/certs:/data/test-certs:ro" "$image"
started=true
"$runtime" exec -i "$name" python3 - <<'PY'
import ssl
import time
import urllib.request

tls = ssl.create_default_context(cafile="/data/test-certs/fullchain.pem")
for attempt in range(30):
    try:
        with urllib.request.urlopen("https://localhost:8443/healthz", context=tls, timeout=2):
            break
    except OSError:
        if attempt == 29:
            raise
        time.sleep(1)
PY
"$runtime" exec -i "$name" python3 - < tests/test_local.py
