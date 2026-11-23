#!/bin/sh
# Deploy the public relay on a Debian 13 VPS. Run as the SSH user or root.
set -eu
fail() { echo "$*" >&2; exit 1; }
usage='用法：sh install.sh relay.example.com（请替换为已指向 VPS 的自有域名）'
[ "$#" -eq 1 ] || fail "$usage"
# Reject IPs and URLs before asking for sudo or installing packages.
case "${1%.}" in
    *[!a-zA-Z0-9.-]*|*.) fail "$usage" ;;
    *.*[a-zA-Z]*) ;;
    *) fail "$usage" ;;
esac
launch_dir=$(pwd -P)
if [ "$(id -u)" -ne 0 ]; then
    exec sudo -- sh -c 'cd -- "$1" && shift && exec sh "$@"' sh "$launch_dir" "$0" "$@"
fi
. /etc/os-release
[ "$ID:$VERSION_ID" = debian:13 ] || fail '一键安装适用于 Debian 13。其他系统请参阅 README 的手动部署。'
exec 9>/run/lock/vibedesk-relay-install.lock
flock -n 9 || fail '另一项中转安装正在运行。'

app=${launch_dir%/}/vibedesk-relay
# Allow a rerun from the installed project as well as from its parent.
if [ "${launch_dir##*/}" = vibedesk-relay ] && [ -f .env ] && [ -f compose.yaml ] && [ -f scripts/endpoint.py ]; then
    app=$launch_dir
fi
app=$(realpath -m -- "$app")
repo=https://github.com/ApeMotion/VibeDesk-Relay.git
owner=${SUDO_USER:-root}
owner_id=$(id -u "$owner")
packages=''
for tool in git curl python3 certbot; do
    command -v "$tool" >/dev/null || packages="$packages $tool"
done
if ! command -v docker >/dev/null; then
    packages="$packages docker.io docker-cli docker-compose docker-buildx"
else
    docker compose version >/dev/null 2>&1 || packages="$packages docker-compose"
    docker buildx version >/dev/null 2>&1 || packages="$packages docker-buildx"
fi
if [ -n "$packages" ]; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates $packages
fi
systemctl enable --now docker.service certbot.timer
docker compose version >/dev/null
installed=$(docker ps -a --last 1 --filter label=com.docker.compose.project=vibedesk-relay \
    --filter label=com.docker.compose.service=relay --filter label=com.docker.compose.oneoff=False \
    --format '{{.Label "com.docker.compose.project.working_dir"}}')
if [ -n "$installed" ] && [ "$(realpath -m -- "$installed")" != "$app" ]; then
    fail "本机已有中转安装在 $installed。请进入该目录重新运行安装脚本。"
fi
if [ ! -f "$app/compose.yaml" ] && [ ! -e "$app/.git" ]; then
    # A sudo-created checkout must be traversable by the SSH user.
    prior_mask=$(umask)
    umask 022
    source_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
    if [ -e "$source_dir/.git" ] && [ -f "$source_dir/scripts/endpoint.py" ]; then
        git -c safe.directory="$source_dir" -c safe.directory="$source_dir/.git" \
            clone --no-local --single-branch --no-tags -- "$source_dir" "$app"
        git -C "$app" remote set-url origin "$repo"
    else
        git clone "$repo" "$app"
    fi
    umask "$prior_mask"
fi
cd "$app"
quoted_app=$(python3 -c 'import shlex, sys; print(shlex.quote(sys.argv[1]))' "$app")
if [ ! -f scripts/renew-cert.sh ] && [ -d .git ]; then
    case "$(git -c safe.directory="$app" config --get remote.origin.url)" in
        "$repo"|git@github.com:ApeMotion/VibeDesk-Relay.git) git -c safe.directory="$app" pull --ff-only ;;
    esac
fi
[ -f scripts/renew-cert.sh ] || fail "请将 $app 的 VibeDesk-Relay 仓库更新到支持当前安装脚本的版本。"
endpoint=$(python3 scripts/endpoint.py "$1")
domain=${endpoint% *}
ip=${endpoint##* }
if [ -e .env ]; then
    configuration=$(docker compose config --format json)
    existing=$(printf '%s\n' "$configuration" | python3 -c 'import json,sys; e=json.load(sys.stdin)["services"]["relay"]["environment"]; print(e["RELAY_DOMAIN"].lower().removesuffix("."), e["RELAY_PUBLIC_IP"])')
    [ "$existing" = "$endpoint" ] || fail "已有部署使用 $existing。如需迁移请先备份并检查 $app/.env。"
fi

lineage=/etc/letsencrypt/live/$domain
if [ ! -s "$lineage/fullchain.pem" ] && { [ ! -s certs/fullchain.pem ] || [ ! -s certs/privkey.pem ]; }; then
    echo "正在为 $domain 申请证书。请保持 TCP 80 可访问。"
    certbot certonly --standalone --non-interactive --agree-tos --register-unsafely-without-email \
        --cert-name "$domain" -d "$domain"
fi
umask 077
scratch=$(mktemp -d "$app/.install.XXXXXXXX")
trap 'rm -rf "$scratch"' EXIT
if [ ! -e .env ]; then
    sed -e "s/^RELAY_DOMAIN=.*/RELAY_DOMAIN=$domain/" -e "s/^RELAY_PUBLIC_IP=.*/RELAY_PUBLIC_IP=$ip/" \
        .env.example > "$scratch/env"
    mv "$scratch/env" .env
fi
if [ -s "$lineage/fullchain.pem" ]; then
    RENEWED_LINEAGE="$lineage" ./scripts/install-cert.sh --register
    certbot renew --cert-name "$domain" --non-interactive --no-random-sleep-on-renew
fi
docker compose up -d --build
echo '正在等待中转 HTTPS 服务启动…'
if ! curl -fsS --retry 10 --retry-connrefused --retry-delay 2 --max-time 5 \
    --resolve "$domain:8443:127.0.0.1" "https://$domain:8443/healthz" >/dev/null 2>"$scratch/health.log"; then
    cat "$scratch/health.log" >&2
    fail "HTTPS 健康检查失败。请在 VPS 运行：cd $quoted_app && docker compose logs --tail 80 relay"
fi

relay() { docker compose exec -T relay python3 /opt/vibedesk/relay.py "$@"; }
attempt=0
until status=$(relay status 2>/dev/null); do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 10 ] || fail "TURN 启动失败，请在 $app 查看 docker compose logs relay。"
    sleep 1
done
service_id=$(printf '%s\n' "$status" | python3 -c 'import json,sys,uuid; print(uuid.UUID(json.load(sys.stdin)["serviceId"]))')
# A replacement data volume gets its own pair. Only the SSH user can read the files.
grants=/var/lib/vibedesk-relay/grants/$domain/$service_id
install -d -m 0755 /var/lib/vibedesk-relay /var/lib/vibedesk-relay/grants "/var/lib/vibedesk-relay/grants/$domain" "$grants"
for device in controller host; do
    file="$grants/$device.vibedesk-relay.json"
    if [ ! -s "$file" ]; then
        tmp=$(mktemp "$grants/.grant.XXXXXXXX")
        if relay grant "$device" > "$tmp"; then
            chown "$owner_id" "$tmp"
            mv -f "$tmp" "$file"
        else
            rm -f "$tmp"
            fail "生成 $device 授权失败，可重跑同一命令。"
        fi
    else
        chown "$owner_id" "$file"
    fi
done
cat <<DONE

中转已就绪：https://$domain:8443
每台设备只复制自己那一段 JSON（从 { 到 }）。在 VibeDesk「自建中转」中粘贴，或存为 .json 文件后导入。

控制端（controller.vibedesk-relay.json）
DONE
python3 -m json.tool "$grants/controller.vibedesk-relay.json"
printf '\n被控端（host.vibedesk-relay.json）\n'
python3 -m json.tool "$grants/host.vibedesk-relay.json"
cat <<DONE

新增设备时，在 VPS 执行以下命令，将 MacBook-Air 换成设备名称。每次执行都会签发并显示一份新授权。
cd $quoted_app && docker compose exec -T relay python3 /opt/vibedesk/relay.py grant "MacBook-Air"
VPS 上的 Compose 目录：$app
查看设备授权：cd $quoted_app && docker compose exec -T relay python3 /opt/vibedesk/relay.py list
普通管理员如需提权，在 docker 前加 sudo。
DONE
