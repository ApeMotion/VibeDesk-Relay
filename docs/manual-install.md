# 手动部署 VibeDesk Relay

需要自行管理 Docker、证书或设备授权时，可按下列步骤操作。域名、网络端口和两台 Mac 的设置见[部署首页](../README.md)。云厂商使用一对一 NAT 时，请将首页列出的端口映射到 VPS 的相同端口。

## 安装并配置服务

在服务器上以 root 执行下列命令。切换到 root 后，先进入计划存放中转的上级目录。Debian 13 提供 Docker 和 Compose 软件包。[Debian Compose 软件包](https://packages.debian.org/trixie/docker-compose)

```sh
apt-get update
apt-get install -y git curl ca-certificates docker.io docker-cli docker-compose docker-buildx certbot
systemctl enable --now docker.service certbot.timer
docker compose version
git clone https://github.com/ApeMotion/VibeDesk-Relay.git ./vibedesk-relay
cd ./vibedesk-relay
cp .env.example .env
```

编辑当前安装目录中的 `.env`，将下面两项改成**自己的域名和公网 IPv4**。示例 IP 是文档专用地址。单网卡服务器通常可以让 `RELAY_LISTEN_IP` 留空；多网卡时可明确指定本机接收中转流量的网卡 IPv4。

```dotenv
RELAY_DOMAIN=relay.example.com
RELAY_PUBLIC_IP=203.0.113.10
```

旧部署若在 `RELAY_DOMAIN` 填了纯 IP，升级前先将它改为已绑定的域名，并按下节安装与域名匹配的证书。归档旧的设备授权 JSON，再按「给两台设备分别授权」生成新配置，分别导入两台设备。

## 申请证书并启动

以下命令在服务器的仓库目录执行，从刚填好的配置中读取域名。等 DNS 生效且 TCP 80 可用后，按 Certbot 的提示申请证书。随后注册部署钩子，将证书安装到当前项目，并在续期成功后重启运行中的中转。[Certbot 续期说明](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates)

```sh
. ./.env
docker compose config --quiet
certbot certonly --standalone --cert-name "$RELAY_DOMAIN" -d "$RELAY_DOMAIN"
RENEWED_LINEAGE="/etc/letsencrypt/live/$RELAY_DOMAIN" ./scripts/install-cert.sh --register
```

如果同一台主机已有网站占用 TCP 80，可以沿用为该域名签发证书的现有流程。将它的完整证书链与私钥以 `fullchain.pem` 和 `privkey.pem` 放在同一目录，通过 `RENEWED_LINEAGE=/实际证书目录 ./scripts/install-cert.sh` 安装，续期后再次调用。脚本将文件复制到 `certs/`，供容器内的用户 65534 读取。

```sh
. ./.env
test -s certs/fullchain.pem && test -s certs/privkey.pem && docker compose up -d --build
docker compose ps
curl --fail --retry 8 --retry-connrefused --retry-delay 2 \
  --resolve "$RELAY_DOMAIN:8443:127.0.0.1" "https://$RELAY_DOMAIN:8443/healthz"
```

期望响应为 `{"status":"ok"}`。再从自己电脑上访问 `https://relay.example.com:8443/healthz`（换成实际域名），确认公网也返回相同结果。使用 Certbot 的部署可运行 `certbot renew --cert-name "$RELAY_DOMAIN" --dry-run` 检查未来的续期。

## 给两台设备分别授权

在 VPS 的中转安装目录执行以下命令。第一条授权命令显示控制端的完整 JSON，第二条显示被控端的完整 JSON。每条命令每执行一次都会创建一份新授权。

```sh
docker compose exec -T relay python3 /opt/vibedesk/relay.py grant "controller"
docker compose exec -T relay python3 /opt/vibedesk/relay.py grant "host"
```

从 VPS 终端复制属于该设备的整段 JSON，在对应设备的 VibeDesk「自建中转」中粘贴，或将其保存为纯文本 `.json` 文件后导入。以后每新增一台设备，用该设备的名称再执行一次 `grant`。两端的连接设置见[部署首页](../README.md#连接两台-mac)。
