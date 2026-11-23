# VibeDesk Relay

在自己的 Linux VPS 上部署 VibeDesk 中转，让两台设备在无法直连时仍能远程连接。VibeDesk 客户端继续通过现有账号和协调服务建立会话，通过 HTTPS 向你的服务器申请短期凭据，再经 coturn 转发加密的远控流量。设备授权、撤销和流量费用由服务器所有者管理。

以下以 **一台全新的 Debian 13 VPS 和两台 Mac** 为例。服务器有固定公网 IPv4；两台设备已安装带有「自建中转」入口的 VibeDesk，并登录同一账号。其他 Linux 发行版准备好 Docker Engine、Docker Compose v2 和 Certbot 后，可以从克隆仓库开始。

## 1. 配置域名和防火墙

为中转选一个域名，例如 `relay.example.com`，将它的 **A 记录**指向 VPS 的公网 IPv4。用 Cloudflare 管理 DNS 时选择 **仅 DNS（DNS only）**。在云安全组和主机防火墙开放下列端口。

| 协议与端口 | 用途 |
| --- | --- |
| TCP 80 入站 | 使用下文的 Certbot standalone 方式签发和续期证书 |
| TCP 8443 入站 | VibeDesk 的 HTTPS 接入、授权和健康检查 |
| UDP 3478 入站 | TURN/UDP |
| TCP 5349 入站 | UDP 受限时使用 TURN/TLS |
| UDP 49160–49259 双向 | 已授权会话的数据转发 |

保留 SSH 管理入口。云厂商使用一对一 NAT 时，将表中的端口映射到 VPS 的相同端口，公网 IPv4 填写云厂商分配的地址。管理端口 5766 仅监听回环地址。Mac 客户端使用 TURN/TLS 时需要与证书匹配的域名。

## 2. 安装并配置服务

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

## 3. 申请证书并启动

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

## 4. 给两台设备分别授权

**每台设备一份 JSON。** 以下命令分别授权控制端和被控端。文件名是管理标签，两台设备可以都是 Mac。这段命令可重复执行，已经写好的授权文件会保留。

```sh
cd /opt/vibedesk-relay
umask 077
grant_dir=/root/vibedesk-relay-grants
install -d -m 0700 "$grant_dir"
for device in controller host; do
  file="$grant_dir/$device.vibedesk-relay.json"
  if [ ! -s "$file" ]; then
    docker compose exec -T relay python3 /opt/vibedesk/relay.py grant "$device" > "$file.tmp" &&
      mv "$file.tmp" "$file"
  fi
done
```

将每份 JSON 私下交给对应的设备持有人。举例，在控制端 Mac 的终端执行下列命令。把 `relay.example.com` 换成服务器登录地址；SSH 使用指定私钥时给 `scp` 加上 `-i 私钥路径`。在被控端同样操作，文件名换成 `host.vibedesk-relay.json`。

```sh
scp root@relay.example.com:/root/vibedesk-relay-grants/controller.vibedesk-relay.json ~/Downloads/
```

## 5. 在 VibeDesk 中验证连接

1. **两台设备**都打开「自建中转」→「导入配置」，各自选择自己的 JSON，勾选「启用此中转」。可先点「测试本机接入」检查设备授权。
2. **被控 Mac** 勾选「允许通过中转连入此电脑」并保存。在「允许远程控制」页面开启远控，按提示授予 macOS 权限，保持唤醒。
3. **控制端 Mac** 把「公网连接策略」选为「仅中转」，保存。从设备列表的 **互联网 P2P** 分组选择被控电脑，按提示输入主机授权码并连接。
4. **验收**：会话中显示 **「自建中转」和你的服务名称**。打开一个桌面或应用，确认能看到画面并操作。要验证跨网络效果，可以把一端切换到手机热点。

配置协议为 v1。客户端需要提供「自建中转」设置，账号协调服务需要支持自建中转协商。控制端也可使用具备该功能的 iOS、iPadOS、Android 或 Windows 客户端；被控端使用支持远控的 macOS 或 Windows 客户端。

## 连接问题

| 现象 | 检查方法 |
| --- | --- |
| HTTPS 健康检查失败 | 检查 DNS 指向、云防火墙 TCP 8443 和域名证书。运行 `docker compose logs --tail 80 relay` 查看启动错误。 |
| 本机测试成功，两台设备连不上 | 确认两端使用同一台中转签发的**不同授权文件**，被控端允许中转连入，并从「互联网 P2P」分组发起连接。 |
| 提示协调服务需更新 | 核对客户端版本和账号协调服务兼容性。重建中转不会更新账号协调服务。 |
| 能获取凭据，但无画面 | 从 VPS 以外的网络运行下方测试，分别核对 UDP 3478 与 TCP 5349。若两项都成功，再检查中转 UDP 端口范围的双向放行及公网 NAT 映射。 |
| 设备授权被拒绝 | 在 VPS 运行下文的 `list`，核对设备是否被撤权；轮换授权后需在对应客户端重新导入并保存。 |

在外部电脑下载本仓库的 `check_access.py`，与该设备的 JSON 放在同一目录，执行下列命令。它会实际申请并释放中转地址；两个参数可分别定位受限的网络路径。

```sh
python3 check_access.py controller.vibedesk-relay.json --transport udp
python3 check_access.py controller.vibedesk-relay.json --transport tls
```

## 维护、更新与备份

在部署目录操作。前两条命令查看设备和使用情况；最后一条用于撤销指定设备，执行前将 `DEVICE_ID` 替换为 `list` 中的设备 ID。

```sh
docker compose exec -T relay python3 /opt/vibedesk/relay.py list
docker compose exec -T relay python3 /opt/vibedesk/relay.py status
docker compose exec -T relay python3 /opt/vibedesk/relay.py revoke DEVICE_ID
```

- **撤权与轮换**。撤权会终止涉及该设备的中转分配。轮换时先签发新的设备配置，在设备上导入并验证，然后撤销旧设备 ID。
- **容量**。默认最多 64 个同时活动的 TURN 分配，一次远控可能占用多个。可在 `.env` 里调整端口范围、分配数和带宽上限，改动后执行 `docker compose up -d`。实际费用以 VPS 提供商的账单为准。
- **备份与恢复**。服务 ID、令牌摘要与撤权记录保存在 Compose 的 `relay-data` 命名卷中。先执行 `docker compose stop relay`，备份卷、`.env`、证书及其续期配置，再执行 `docker compose start relay`。恢复时先停服，将原卷恢复到相同 Compose 项目并保留文件属主及权限，再启动。若恢复了较早备份，先补上之后的撤权记录再开放服务。全新数据卷会生成新的服务 ID。
- **更新**。备份后运行 `git pull --ff-only` 和 `docker compose up -d --build`，复核健康检查和实际中转连接。升级或加载新证书重启服务时，进行中的中转会话可能中断。
- **停用**。`docker compose down` 会停止容器并保留授权数据。`docker compose down -v` 会删除数据卷，已有设备需重新授权。

## 本地测试

运行独立测试需要 OpenSSL，以及 Docker 或 macOS 上的 [Apple container](https://github.com/apple/container)。在仓库根目录选择对应命令。测试会构建镜像，在临时容器内检查 UDP/TLS、双向数据、配额、凭据续期与撤权，不发布宿主机端口。

```sh
# Linux
./tests/smoke.sh

# macOS，先启动 Apple 的容器服务
container system start
CONTAINER_CLI=container ./tests/smoke.sh
```

## 许可证

VibeDesk Relay 的源码与部署脚本使用 GNU GPLv3。基础镜像使用 coturn，随附上游版权与许可声明。镜像中的 Python、Alpine 软件包和其他依赖保留各自的许可。[GPLv3](LICENSE) · [coturn 许可证](licenses/coturn.txt) · [coturn 项目](https://github.com/coturn/coturn)
