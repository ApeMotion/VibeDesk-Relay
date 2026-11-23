# VibeDesk Relay

在自己的 VPS 上部署中转，让两台登录同一 VibeDesk 账号的 Mac 在无法直连时也能远控。中转负责转发加密流量，设备发现和会话协商继续使用 VibeDesk 的账号服务。[下载 VibeDesk](https://vibedesk.app/downloads.html)

## 一条命令部署

1. **绑定自己的域名。** 例如，你拥有 `example.com`，可以添加一条 `A` 记录，名称填 `relay`，值填 VPS 的公网 IPv4。这样中转域名就是 `relay.example.com`。也可以直接使用根域名，记录名称填 `@`。该名称应只解析到这台 VPS 的公网 IPv4。使用 Cloudflare 时选择「仅 DNS」（灰云）。[Cloudflare DNS 说明](https://developers.cloudflare.com/dns/proxy-status/)
2. **开放端口。** 使用有固定公网 IPv4 的 **Debian 13 VPS**，在云安全组和主机防火墙放行 **TCP 80、8443、5349** 与 **UDP 3478、49160–49259**，其中 UDP 端口范围需允许双向通信。保留 SSH 端口，并让 TCP 80 可用于证书签发和续期。

等 DNS 生效后，通过 SSH 登录 VPS，把下面的 `relay.example.com` 换成刚绑定的域名。普通管理员和 root 均可运行。执行命令即同意向 Let’s Encrypt 申请公开证书并接受其[订户协议](https://letsencrypt.org/repository/)。

```sh
curl -fsSLo vibedesk-relay-install.sh https://raw.githubusercontent.com/ApeMotion/VibeDesk-Relay/main/install.sh && sh vibedesk-relay-install.sh relay.example.com
```

脚本检查域名解析，安装 Docker Compose，为该域名申请证书并配置自动续期，启动中转，再生成两份使用该域名的设备授权。

**取回授权**：安装完成后，复制终端最后给出的 `scp` 命令，在自己的 Mac 上运行。将 `controller.vibedesk-relay.json` 留给控制端，把 `host.vibedesk-relay.json` 通过隔空投送等私密方式交给被控端。若 SSH 使用自定义端口或私钥，给 `scp` 加上平时的 `-P 端口` 或 `-i 私钥路径`。同一数据卷重跑安装命令会保留同一对授权，也会再次显示取件命令。

如果已经在 VPS 上克隆了本仓库，直接在仓库目录执行 `sh install.sh relay.example.com`，同样换成自己的域名。其他 Linux 发行版、已有网站占用 TCP 80，或希望自行管理证书时，可按[手动部署指南](docs/manual-install.md)操作。

## 连接两台 Mac

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
| 设备列表显示在线，连接时提示被控端离线 | 唤醒被控电脑并打开 VibeDesk。等正在使用的会话结束后，在被控端将「允许远程控制」关闭再打开，重新向协调服务注册，然后重试。 |
| 提示协调服务需更新 | 核对客户端版本和账号协调服务兼容性。重建中转不会更新账号协调服务。 |
| 能获取凭据，但无画面 | 从 VPS 以外的网络运行下方测试，分别核对 UDP 3478 与 TCP 5349。若两项都成功，再检查中转 UDP 端口范围的双向放行及公网 NAT 映射。 |
| 设备授权被拒绝 | 在 VPS 运行下文的 `list`，核对设备是否被撤权；轮换授权后需在对应客户端重新导入并保存。 |

在外部电脑下载本仓库的 `check_access.py`，与该设备的 JSON 放在同一目录，执行下列命令。它会实际申请并释放中转地址；两个参数可分别定位受限的网络路径。

```sh
python3 check_access.py controller.vibedesk-relay.json --transport udp
python3 check_access.py controller.vibedesk-relay.json --transport tls
```

## 维护、更新与备份

在服务器上进入 `/opt/vibedesk-relay`，以有 Docker 管理权限的账号操作。前两条命令查看设备和使用情况；最后一条用于撤销指定设备，执行前将 `DEVICE_ID` 替换为 `list` 中的设备 ID。

```sh
docker compose exec -T relay python3 /opt/vibedesk/relay.py list
docker compose exec -T relay python3 /opt/vibedesk/relay.py status
docker compose exec -T relay python3 /opt/vibedesk/relay.py revoke DEVICE_ID
```

- **撤权与轮换**。撤权会终止涉及该设备的中转分配。轮换时先签发新的设备配置，在设备上导入并验证，然后撤销旧设备 ID。
- **容量**。默认最多 64 个同时活动的 TURN 分配，一次远控可能占用多个。可在 `.env` 里调整端口范围、分配数和带宽上限，改动后执行 `docker compose up -d`。实际费用以 VPS 提供商的账单为准。
- **备份与恢复**。服务 ID、令牌摘要与撤权记录保存在 Compose 的 `relay-data` 命名卷中。先执行 `docker compose stop relay`，备份卷、`.env`、证书及其续期配置。一键安装还应备份 `/var/lib/vibedesk-relay/grants/` 中的设备授权，再执行 `docker compose start relay`。恢复时先停服，将原卷恢复到相同 Compose 项目并保留文件属主及权限，再启动。若恢复了较早备份，先补上之后的撤权记录再开放服务。全新数据卷会生成新的服务 ID；重跑安装命令后，取回新文件并在两台设备重新导入。
- **更新**。备份后运行 `git pull --ff-only` 和 `docker compose up -d --build`，复核健康检查和实际中转连接。升级或加载新证书重启服务时，进行中的中转会话可能中断。
- **停用**。`docker compose down` 会停止容器并保留授权数据。`docker compose down -v` 会删除数据卷，已有设备需重新授权。

## 本地测试

运行 `python3 -m unittest discover -s tests` 可检查授权文件和凭据格式。验证实际转发还需要 OpenSSL，以及 Docker 或 macOS 上的 [Apple container](https://github.com/apple/container)。在仓库根目录选择对应命令。测试会构建镜像，在临时容器内检查 UDP/TLS、双向数据、配额、凭据续期与撤权，不发布宿主机端口。

```sh
# Linux
./tests/smoke.sh

# macOS，先启动 Apple 的容器服务
container system start
CONTAINER_CLI=container ./tests/smoke.sh
```

## 许可证

VibeDesk Relay 的源码与部署脚本使用 GNU GPLv3。基础镜像使用 coturn，随附上游版权与许可声明。镜像中的 Python、Alpine 软件包和其他依赖保留各自的许可。[GPLv3](LICENSE) · [coturn 许可证](licenses/coturn.txt) · [coturn 项目](https://github.com/coturn/coturn)
