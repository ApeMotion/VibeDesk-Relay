"""Resolve the operator's IP or DNS name to the IPv4 endpoint used by Compose."""
import ipaddress
import re
import socket
import sys


def resolve(value):
    host = value.lower().rstrip(".")
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError:
        if (len(host) > 253 or "." not in host or host.rsplit(".", 1)[1].isdigit()
                or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                           for label in host.split("."))):
            raise ValueError("请填写 VPS 公网 IPv4 或完整域名。")
        address = None
    else:
        host = f"{str(address).replace('.', '-')}.sslip.io"
    records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    ips = {ipaddress.ip_address(record[4][0]) for record in records}
    if len(ips) != 1 or (address is not None and ips != {address}):
        raise ValueError(f"{host} 需要一条指向 VPS 的 A 记录；请检查 DNS、AAAA 记录和代理设置。")
    ip = ips.pop()
    if ip.version != 4 or not ip.is_global or ip.is_multicast:
        raise ValueError(f"{host} 需要解析到公网 IPv4。")
    return host, str(ip)


if __name__ == "__main__":
    try:
        print(*resolve(sys.argv[1]))
    except (OSError, ValueError) as error:
        sys.exit(f"中转地址检查失败：{error}")
