# 手动部署 VibeDesk Relay

需要自行管理 Docker、证书或设备授权时，可按下列步骤操作。域名、网络端口和两台 Mac 的设置见[部署首页](../README.md)。云厂商使用一对一 NAT 时，请将首页列出的端口映射到 VPS 的相同端口。

## 安装并配置服务

在服务器上以 root 执行下列命令。已有管理员账号可以先运行 `sudo -i`。Debian 13 提供 Docker 和 Compose 软件包。[Debian Compose 软件包](https://packages.debian.org/trixie/docker-compose)

```sh
apt-get update
apt-get install -y git curl ca-certificates docker.io docker-cli docker-compose docker-buildx certbot
systemctl enable --now docker.service certbot.timer
docker compose version
git clone https://github.com/ApeMotion/VibeDesk-Relay.git /opt/vibedesk-relay
cd /opt/vibedesk-relay
cp .env.example .env
```

编辑 `/opt/vibedesk-relay/.env`，将下面两项改成**自己的域名和公网 IPv4**。示例 IP 是文档专用地址。单网卡服务器通常可以让 `RELAY_LISTEN_IP` 留空；多网卡时可明确指定本机接收中转流量的网卡 IPv4。

```dotenv
RELAY_DOMAIN=relay.example.com
RELAY_PUBLIC_IP=203.0.113.10
```

旧部署若在 `RELAY_DOMAIN` 填了纯 IP，升级前先将它改为已绑定的域名，并按下节安装与域名匹配的证书。归档旧的设备授权 JSON，再按「给两台设备分别授权」生成新配置，分别导入两台设备。

## 申请证书并启动

以下命令在服务器的仓库目录执行，从刚填好的配置中读取域名。等 DNS 生效且 TCP 80 可用后，按 Certbot 的提示申请证书。仓库中的部署钩子会安装证书，并在续期成功后重启运行中的中转。[Certbot 续期说明](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates)

```sh
cd /opt/vibedesk-relay
. ./.env
docker compose config --quiet
certbot certonly --standalone --cert-name "$RELAY_DOMAIN" -d "$RELAY_DOMAIN" \
  --deploy-hook /opt/vibedesk-relay/scripts/install-cert.sh
```

如果同一台主机已有网站占用 TCP 80，可以沿用为该域名签发证书的现有流程。将它的完整证书链与私钥以 `fullchain.pem` 和 `privkey.pem` 放在同一目录，通过 `RENEWED_LINEAGE=/实际证书目录 /opt/vibedesk-relay/scripts/install-cert.sh` 安装，续期后再次调用。脚本将文件复制到 `certs/`，供容器内的用户 65534 读取。

```sh
cd /opt/vibedesk-relay
. ./.env
test -s certs/fullchain.pem && test -s certs/privkey.pem && docker compose up -d --build
docker compose ps
curl --fail --retry 8 --retry-connrefused --retry-delay 2 \
  --resolve "$RELAY_DOMAIN:8443:127.0.0.1" "https://$RELAY_DOMAIN:8443/healthz"
```

期望响应为 `{"status":"ok"}`。再从自己电脑上访问 `https://relay.example.com:8443/healthz`（换成实际域名），确认公网也返回相同结果。使用 Certbot 的部署可运行 `certbot renew --cert-name "$RELAY_DOMAIN" --dry-run` 检查未来的续期。

## 给两台设备分别授权

**每台设备一份 JSON。** 以下命令在 VPS 上以你平时的 SSH 登录账号执行。如果之前用 `sudo -i` 切到了 root，先用 `exit` 回到原账号。授权文件保存在该账号的私有目录，管理员可用同一账号取回。同一服务重复执行时会保留已有文件。若已重建服务数据卷，先将旧的授权目录改名归档，以便生成与新服务匹配的文件。

```sh
umask 077
grant_dir="$HOME/vibedesk-relay-grants"
install -d -m 0700 "$grant_dir"
if [ "$(id -u)" -eq 0 ]; then
  compose() { docker compose "$@"; }
else
  compose() { sudo docker compose "$@"; }
fi
for device in controller host; do
  file="$grant_dir/$device.vibedesk-relay.json"
  if [ ! -s "$file" ]; then
    compose --env-file /opt/vibedesk-relay/.env -f /opt/vibedesk-relay/compose.yaml \
      exec -T relay python3 /opt/vibedesk/relay.py grant "$device" > "$file.tmp" &&
      mv "$file.tmp" "$file"
  fi
done
```

在管理员自己的 Mac 上，使用刚才的 SSH 用户名和地址替换下方的 `admin@relay.example.com`。远程文件路径相对于该账号的家目录。两份文件取回后，将 `controller` 留给控制端，将 `host` 通过隔空投送等私密方式交给被控端。设备持有人只需要各自的文件。SSH 使用指定私钥时可给 `scp` 加上 `-i 私钥路径`。

```sh
dest="$HOME/Downloads/relay.example.com"
mkdir -p -m 700 "$dest"
scp -p admin@relay.example.com:vibedesk-relay-grants/controller.vibedesk-relay.json \
       admin@relay.example.com:vibedesk-relay-grants/host.vibedesk-relay.json "$dest/"
```
