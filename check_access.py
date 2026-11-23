"""Check an operator-issued device grant and real TURN allocations from a client network."""
import argparse
import contextlib
import hashlib
import hmac
import json
import math
import os
import re
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

COOKIE = 0x2112A442


def attr(kind, value):
    return struct.pack("!HH", kind, len(value)) + value + bytes(-len(value) % 4)


def read_exact(stream, length):
    data = bytearray()
    while len(data) < length:
        chunk = stream.recv(length - len(data))
        if not chunk:
            raise OSError("TURN closed the connection")
        data.extend(chunk)
    return data


def encode(method, attrs, tx, key=None):
    head = struct.pack("!HHI12s", method, len(attrs) + (24 if key else 0), COOKIE, tx)
    request = head + attrs
    if key:
        request += attr(8, hmac.digest(key, request, "sha1"))
    return request


def receive(stream, tls):
    if tls:
        head = read_exact(stream, 20)
        response = head + read_exact(stream, struct.unpack_from("!H", head, 2)[0])
    else:
        response = stream.recv(4096)
    kind, length, magic, response_tx = struct.unpack_from("!HHI12s", response)
    if magic != COOKIE or len(response) != 20 + length:
        raise OSError("Invalid TURN response")
    values = {}
    offset = 20
    while offset < len(response):
        field, size = struct.unpack_from("!HH", response, offset)
        offset += 4
        values[field] = response[offset:offset + size]
        offset += (size + 3) & ~3
    return kind, values, response_tx


def exchange(stream, tls, method, attrs, key=None):
    tx = os.urandom(12)
    stream.sendall(encode(method, attrs, tx, key))
    kind, values, response_tx = receive(stream, tls)
    if response_tx != tx:
        raise OSError("Unexpected TURN transaction")
    return kind, values


@contextlib.contextmanager
def allocated(ice, host, address, context, tls):
    port = 5349 if tls else 3478
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM if tls else socket.SOCK_DGRAM) as sock:
        sock.settimeout(4)
        sock.connect((address, port))
        stream = context.wrap_socket(sock, server_hostname=host) if tls else sock
        with stream:
            transport = attr(0x19, bytes([17, 0, 0, 0]))
            kind, challenge = exchange(stream, tls, 3, transport)
            if kind != 0x113 or challenge.get(9, b"")[2:4] != bytes([4, 1]):
                raise OSError("TURN authentication challenge expected")
            username = ice["username"].encode()
            realm = challenge[0x14]
            auth = attr(6, username) + attr(0x14, realm) + attr(0x15, challenge[0x15])
            key = hashlib.md5(b":".join([username, realm, ice["credential"].encode()]), usedforsecurity=False).digest()
            kind, result = exchange(stream, tls, 3, transport + auth, key)
            if kind != 0x103 or 0x16 not in result:
                error = result.get(9, bytes(4))
                raise OSError(f"TURN allocation rejected ({error[2] * 100 + error[3]})")
            try:
                yield stream, auth, key, result[0x16]
            finally:
                # A revoked or expired allocation may have already closed its TLS connection.
                with contextlib.suppress(OSError):
                    exchange(stream, tls, 4, attr(0x0d, bytes(4)) + auth, key)


def allocation(ice, host, address, context, tls):
    with allocated(ice, host, address, context, tls):
        pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def read_object(source):
    data = source.read(16385)
    if len(data) > 16384:
        raise ValueError("Relay JSON exceeds 16 KiB")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Expected a relay JSON object")
    return value


def grant_endpoint(config):
    if type(config.get("version")) is not int or config["version"] != 1:
        raise ValueError("Unsupported device configuration version")
    if not all(isinstance(config.get(field), str) for field in ("name", "token", "serviceId", "endpoint")):
        raise ValueError("Invalid device configuration fields")
    if not (1 <= len(config["name"]) <= 120 and re.fullmatch(r"[A-Za-z0-9_-]{43}", config["token"])):
        raise ValueError("Invalid relay name or device token")
    if not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", config["serviceId"], re.I):
        raise ValueError("Invalid relay service ID")
    endpoint = config["endpoint"]
    url = urllib.parse.urlsplit(endpoint)
    if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
            or url.path not in ("", "/") or "?" in endpoint or "#" in endpoint):
        raise ValueError("Invalid HTTPS endpoint")
    return url


def device_ice_server(lease, service_id, host):
    if lease.get("serviceId") != service_id:
        raise ValueError("Service identity mismatch")
    if lease.get("sessionId") is not None or lease.get("peerAccessId") is not None:
        raise ValueError("Expected a lease for this device only")
    expiry = lease.get("expiresAt")
    if not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= time.time() + 10:
        raise ValueError("Missing or expired relay credentials")
    servers = lease.get("iceServers")
    if not isinstance(servers, list) or len(servers) != 1 or not isinstance(servers[0], dict):
        raise ValueError("Expected one TURN server")
    server = servers[0]
    if not all(isinstance(server.get(field), str) and server[field] for field in ("username", "credential")):
        raise ValueError("Missing TURN username or credential")
    urls = server.get("urls")
    expected = {f"turn:{host}:3478?transport=udp", f"turns:{host}:5349?transport=tcp"}
    if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls) or set(urls) != expected:
        raise ValueError("Advertised TURN URLs do not match the relay endpoint")
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration")
    parser.add_argument("--ca", help="PEM trust anchor for a local test service")
    parser.add_argument("--transport", choices=["udp", "tls", "both"], default="both")
    args = parser.parse_args()
    with open(args.configuration, "rb") as source:
        config = read_object(source)
    url = grant_endpoint(config)
    context = ssl.create_default_context(cafile=args.ca)
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
    req = urllib.request.Request(config["endpoint"].rstrip("/") + "/v1/credentials", headers={"Authorization": "Bearer " + config["token"]})
    with opener.open(req, timeout=5) as response:
        if response.status != 200:
            raise ValueError(f"Credentials endpoint returned HTTP {response.status}")
        lease = read_object(response)
    server = device_ice_server(lease, config["serviceId"], url.hostname)
    for protocol in (["udp", "tls"] if args.transport == "both" else [args.transport]):
        allocation(server, url.hostname, url.hostname, context, protocol == "tls")
        print(f"TURN/{protocol.upper()} authenticated allocation OK")


if __name__ == "__main__":
    main()
