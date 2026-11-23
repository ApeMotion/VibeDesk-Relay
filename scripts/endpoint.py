"""Validate the relay's DNS name and resolve its public IPv4 for installation."""
import ipaddress
import re
import socket
import sys


def domain_name(value):
    host = value.lower().removesuffix(".")
    if (len(host) > 253 or "." not in host or host.rsplit(".", 1)[1].isdigit()
            or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in host.split("."))):
        raise ValueError("请填写已指向 VPS 的完整域名，例如 relay.example.com。")
    return host


def resolve(value):
    host = domain_name(value)
    try:
        records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError(f"无法解析 {host}，请确认 A 记录已指向 VPS 并等待 DNS 生效。") from error
    ips = {ipaddress.ip_address(record[4][0]) for record in records}
    if len(ips) != 1:
        raise ValueError(f"{host} 需要一条指向 VPS 的 A 记录，请检查 DNS、AAAA 记录和代理设置。")
    ip = ips.pop()
    if ip.version != 4 or not ip.is_global or ip.is_multicast:
        raise ValueError(f"{host} 需要解析到公网 IPv4。")
    return host, str(ip)


if __name__ == "__main__":
    try:
        print(*resolve(sys.argv[1]))
    except (OSError, ValueError) as error:
        sys.exit(f"中转地址检查失败：{error}")
