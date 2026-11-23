#!/bin/sh
# The installer supplies the lineage, certificate directory and its identity.
set -eu
[ "${RENEWED_LINEAGE:?}" = "$1" ] || exit 0
cd -P -- "$2"
# Pin the working directory before writing as root beneath a user-chosen path.
if [ "$(stat -c '%d:%i:%u:%a' .)" != "$3" ]; then
    echo '证书目录已变更，请从安装位置重新注册续期钩子。' >&2
    exit 1
fi
if cmp -s "$RENEWED_LINEAGE/fullchain.pem" fullchain.pem &&
   cmp -s "$RENEWED_LINEAGE/privkey.pem" privkey.pem &&
   [ "$(stat -c '%u:%g:%a' fullchain.pem)" = '0:65534:640' ] &&
   [ "$(stat -c '%u:%g:%a' privkey.pem)" = '0:65534:640' ]; then
    exit 0
fi
staging=$(mktemp -d .install.XXXXXXXX)
trap 'rm -rf "$staging"' EXIT
for file in fullchain.pem privkey.pem; do
    install -o root -g 65534 -m 0640 "$RENEWED_LINEAGE/$file" "$staging/$file"
done
mv -f "$staging/fullchain.pem" fullchain.pem
mv -f "$staging/privkey.pem" privkey.pem

# Reload the running relay without evaluating the checkout's Compose file as root.
running=$(docker ps -q --filter label=com.docker.compose.project=vibedesk-relay \
    --filter label=com.docker.compose.service=relay --filter label=com.docker.compose.oneoff=False)
if [ -n "$running" ]; then
    docker restart "$running"
fi
