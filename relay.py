"""VibeDesk relay. Management is local to the container; devices renew over HTTPS."""
import argparse
import contextlib
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(os.environ.get("RELAY_STATE", "/data"))
REALM = "vibedesk"
LEASE = 600
CLI_PORT = 5766
CLI_KEY = Path("/tmp/vibedesk-relay-cli")
CERTS = Path("/data/test-certs" if os.environ.get("RELAY_LOCAL_TEST") == "1" else "/certs")
SCHEMA = """
CREATE TABLE IF NOT EXISTS service(id INTEGER PRIMARY KEY CHECK(id=1), uuid TEXT, secret TEXT);
CREATE TABLE IF NOT EXISTS devices(user TEXT PRIMARY KEY, label TEXT, token_hash TEXT UNIQUE, expires INTEGER DEFAULT 0, revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS leases(name TEXT PRIMARY KEY, device TEXT NOT NULL, expires INTEGER NOT NULL, peer TEXT, connection TEXT);
CREATE TABLE IF NOT EXISTS turnusers_lt(realm varchar(127) default '', name varchar(512), hmackey char(128), PRIMARY KEY(realm,name));
"""


@contextlib.contextmanager
def database():
    db = sqlite3.connect(ROOT / "relay.sqlite", timeout=5)
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not (ROOT / "relay.sqlite").exists():
        with tempfile.NamedTemporaryFile(dir=ROOT) as seed:
            shutil.copyfile("/var/lib/coturn/turndb", seed.name)
            os.link(seed.name, ROOT / "relay.sqlite")
    with database() as db:
        # coturn reads keys on a separate connection while devices renew leases.
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
        columns = {row[1] for row in db.execute("PRAGMA table_info(leases)")}
        for column in ("peer", "connection"):
            if column not in columns:
                db.execute(f"ALTER TABLE leases ADD COLUMN {column} TEXT")
        db.execute("INSERT OR IGNORE INTO service VALUES(1,?,?)", (str(uuid.uuid4()), secrets.token_hex(32)))


def domain():
    value = os.environ["RELAY_DOMAIN"].lower()
    if len(value) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in value.split(".")):
        raise ValueError("Invalid RELAY_DOMAIN")
    return value


def grant(label):
    if not label.strip() or len(label) > 80 or not label.isprintable():
        raise ValueError("Device name must be 1–80 printable characters")
    host = domain()
    # Clients limit the display name to 120 characters; keep the full endpoint.
    name = host if len(host) <= 120 else "…" + host[-119:]
    token = secrets.token_urlsafe(32)
    with database() as db:
        service_id, = db.execute("SELECT uuid FROM service").fetchone()
        db.execute("INSERT INTO devices(user,label,token_hash) VALUES(?,?,?)",
                   ("vd" + uuid.uuid4().hex, label, hashlib.sha256(token.encode()).hexdigest()))
    return dict(version=1, serviceId=service_id, name=name, endpoint=f"https://{host}:8443", token=token)


def credentials(token, peer_access=None, connection=None, nonce=None):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT user FROM devices WHERE token_hash=? AND revoked=0",
                         (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row:
            return None
        user = row[0]
        peer = None
        if peer_access is not None or connection is not None:
            if not re.fullmatch(r"[0-9a-f]{32}", peer_access or "") or not re.fullmatch(
                    r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", connection or ""):
                return None
            other = db.execute("SELECT user FROM devices WHERE token_hash BETWEEN ? AND ? AND revoked=0",
                               (peer_access + "0"*32, peer_access + "f"*32)).fetchone()
            if not other or other[0] == user:
                return None
            peer = other[0]
        service_id, secret = db.execute("SELECT uuid, secret FROM service").fetchone()
        now = int(time.time())
        expires = now - now % (LEASE // 2) + LEASE
        username = f"{user}.{expires}"
        if peer:
            # A live account session renews its own short lease. Keeping its
            # allocation identity until expiry lets native ICE recover a lost allocation.
            active = db.execute("SELECT name FROM leases WHERE device=? AND peer=? AND connection=? AND expires>?",
                                (user, peer, connection, now)).fetchone()
            username = active[0] if active else f"{user}.{peer}.{secrets.randbits(96)}"
            expires = now + LEASE
        password = hmac.new(bytes.fromhex(secret), username.encode(), hashlib.sha256).hexdigest()
        if nonce is not None:
            # The Windows native ICE stack takes its server list at construction.
            # Reserve its two loopback transports up front; only this authenticated
            # request activates the matching allocation, bound to both devices.
            if not peer or not re.fullmatch(r"[0-9]{10}\.[0-9a-f]{32}", nonce):
                return None
            challenge = f"VibeDesk TURN/1\n{connection}\n{peer_access}\n{nonce}".encode()
            password = hmac.new(token.encode(), challenge, hashlib.sha256).hexdigest()
            username = "vd" + hashlib.sha256(password.encode()).hexdigest()[:32] + "." + nonce[:10]
            if not (active and active[0] == username) and abs(now - int(nonce[:10])) > 120:
                return None
        # RFC long-term credential key used by coturn's SQLite backend.
        key = hashlib.md5(f"{username}:{REALM}:{password}".encode(), usedforsecurity=False).hexdigest()
        db.execute("UPDATE devices SET expires=MAX(expires,?) WHERE user=?", (expires, user))
        db.execute("INSERT OR REPLACE INTO leases(name,device,expires,peer,connection) VALUES(?,?,?,?,?)", (username, user, expires, peer, connection))
        db.execute("INSERT OR REPLACE INTO turnusers_lt(realm,name,hmackey) VALUES(?,?,?)", (REALM, username, key))
    return dict(serviceId=service_id, peerAccessId=peer_access, sessionId=connection, expiresAt=expires, iceServers=[dict(
        urls=[f"turn:{domain()}:3478?transport=udp", f"turns:{domain()}:5349?transport=tcp"],
        username=username, credential=password)])


class DeviceAPI(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self.reply(200, {"status": "ok"})
        elif self.path == "/v1/credentials":
            header = self.headers.get("Authorization", "")
            match = re.fullmatch("Bearer ([A-Za-z0-9_-]{43})", header)
            result = credentials(match[1], self.headers.get("X-VibeDesk-Relay-Peer"), self.headers.get("X-VibeDesk-Relay-Session"), self.headers.get("X-VibeDesk-Relay-Nonce")) if match else None
            if result:
                self.reply(200, result)
            else:
                self.reply(403, {"error": "access-denied"})
        else:
            self.reply(404, {"error": "not-found"})


def cli(commands):
    def read_until(sock, marker):
        data = bytearray()
        while not data.rstrip(b"\r\n").endswith(marker):
            chunk = sock.recv(8192)
            if not chunk or len(data) > 8_000_000:
                raise IOError("coturn management response incomplete")
            data.extend(chunk)
        return data.decode("utf8", errors="replace")
    with socket.create_connection(("127.0.0.1", CLI_PORT), timeout=2) as sock:
        read_until(sock, b"Enter password: ")
        sock.sendall(CLI_KEY.read_bytes() + b"\r\n")
        read_until(sock, b"> ")
        output = []
        for cmd in commands:
            sock.sendall(cmd.encode() + b"\r\n")
            output.append(read_until(sock, b"> "))
        return output


def revoke(user):
    with database() as db:
        changed = db.execute("UPDATE devices SET revoked=1 WHERE user=?", (user,)).rowcount
        if not changed:
            raise ValueError("Unknown device ID")
        db.execute("DELETE FROM turnusers_lt WHERE realm=? AND name IN (SELECT name FROM leases WHERE device=? OR peer=?)", (REALM, user, user))
    expire_sessions()


def expire_sessions():
    now = int(time.time())
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM turnusers_lt WHERE realm=? AND name IN (SELECT l.name FROM leases l JOIN devices d ON d.user=l.device LEFT JOIN devices p ON p.user=l.peer WHERE d.revoked=1 OR p.revoked=1 OR l.expires<=?)", (REALM, now))
        db.execute("DELETE FROM leases WHERE expires<=?", (now,))
    # Sessions cache TURN authentication. Removing a DB key alone would leave
    # an existing allocation alive. Repeat the check to catch racing allocations.
    sessions = re.findall(r"id=(\d+), user <(vd[0-9a-f]{32}(?:\.vd[0-9a-f]{32})?\.\d+)>", cli(["ps"])[0])
    with database() as db:
        authorized = {row[0] for row in db.execute(
            "SELECT l.name FROM leases l JOIN devices d ON d.user=l.device LEFT JOIN devices p ON p.user=l.peer "
            "WHERE l.expires>? AND d.revoked=0 AND (l.peer IS NULL OR p.revoked=0)", (now,))}
    commands = ["cs " + sid for sid, name in sessions if name not in authorized]
    if commands:
        cli(commands)


def setting(name, default, maximum):
    value = int(os.environ.get(name, default))
    if not 1 <= value <= maximum:
        raise ValueError(f"Invalid {name}")
    return value


def turn_config():
    domain()
    listen = os.environ.get("RELAY_LISTEN_IP")
    if not listen:
        # Ask the kernel for the default route's local address. UDP connect does not send traffic.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect(("192.0.2.1", 9))
            listen = route.getsockname()[0]
    listen = ipaddress.IPv4Address(listen)
    public = listen if os.environ.get("RELAY_LOCAL_TEST") == "1" else ipaddress.IPv4Address(os.environ["RELAY_PUBLIC_IP"])
    low = setting("RELAY_MIN_PORT", 49160, 65535)
    high = setting("RELAY_MAX_PORT", 49259, 65535)
    if low < 1024 or low > high:
        raise ValueError("Invalid relay port range")
    quota = setting("RELAY_TOTAL_QUOTA", 64, 1000)
    lines = ["lt-cred-mech", "fingerprint", "no-tcp", "no-tcp-relay", "no-multicast-peers", "no-software-attribute",
             "simple-log", "log-file=stdout", "log-min-level=warning",
             "listening-port=3478", "tls-listening-port=5349", f"cert={CERTS}/fullchain.pem", f"pkey={CERTS}/privkey.pem",
             "listening-ip=" + str(listen), "relay-ip=" + str(listen), f"min-port={low}", f"max-port={high}",
             "realm=" + REALM, "userdb=" + str(ROOT / "relay.sqlite"), "pidfile=/tmp/turn.pid",
             f"total-quota={quota}", "user-quota=8", f"cli-max-output-sessions={quota+64}",
             f"max-bps={setting('RELAY_MAX_BPS', 4000000, 1000000000)}",
             f"bps-capacity={setting('RELAY_CAPACITY', 64000000, 1000000000)}",
             "cli", "cli-ip=127.0.0.1", f"cli-port={CLI_PORT}", "cli-password=" + CLI_KEY.read_text()]
    if public != listen:
        lines.append(f"external-ip={public}/{listen}")
    for block in ["0.0.0.0/8","10.0.0.0/8","100.64.0.0/10","127.0.0.0/8","169.254.0.0/16","172.16.0.0/12","192.168.0.0/16","::1/128","fc00::/7","fe80::/10"]:
        net = ipaddress.ip_network(block)
        lines.append(f"denied-peer-ip={net.network_address}-{net.broadcast_address}")
    if os.environ.get("RELAY_LOCAL_TEST") == "1":
        # Permit relay-to-relay on this disposable VM only.
        lines.append(f"allowed-peer-ip={listen}")
    return "\n".join(lines) + "\n"


def serve():
    initialize()
    CLI_KEY.write_text(secrets.token_urlsafe(32))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(CERTS / "fullchain.pem", CERTS / "privkey.pem")
    # Accept TLS on workers; a slow TLS client must not block the lease sweeper.
    class Server(http.server.ThreadingHTTPServer):
        workers = threading.BoundedSemaphore(32)

        def process_request(self, request, address):
            if self.workers.acquire(blocking=False):
                super().process_request(request, address)
            else:
                self.shutdown_request(request)

        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                self.workers.release()

        def get_request(self):
            sock, addr = super().get_request()
            sock.settimeout(5)
            return context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr
    with tempfile.NamedTemporaryFile(mode="w", prefix="vd-turn-", delete=False) as config:
        config.write(turn_config())
    with Server(("", 8443), DeviceAPI) as api:
        api.timeout = 0.5
        child = subprocess.Popen(["turnserver", "-c", config.name])
        stop = False
        def terminate(*_):
            nonlocal stop
            stop = True
        signal.signal(signal.SIGTERM, terminate)
        deadline = time.monotonic() + 2
        last_sweep = time.monotonic()
        try:
            while not stop and child.poll() is None:
                api.handle_request()
                if time.monotonic() >= deadline:
                    try:
                        expire_sessions()
                        last_sweep = time.monotonic()
                        deadline = last_sweep + 5
                    except (OSError, sqlite3.OperationalError):
                        if time.monotonic() - last_sweep > 35:
                            raise RuntimeError("Cannot enforce TURN revocations")
                        deadline = time.monotonic() + 1
        finally:
            child.terminate()
            child.wait(timeout=8)
            os.unlink(config.name)
        if not stop:
            raise RuntimeError("coturn stopped")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ["serve", "list", "status"]:
        sub.add_parser(name)
    sub.add_parser("grant").add_argument("label")
    sub.add_parser("revoke").add_argument("device_id")
    args = parser.parse_args()
    if args.action == "serve":
        serve()
    elif args.action == "grant":
        print(json.dumps(grant(args.label)))
    elif args.action == "revoke":
        revoke(args.device_id)
    elif args.action == "status":
        report = cli(["ps"])[0]
        with database() as db:
            service_id, = db.execute("SELECT uuid FROM service").fetchone()
        usage = re.findall(r"usage: rp=\d+, rb=(\d+), sp=\d+, sb=(\d+)", report)
        print(json.dumps(dict(serviceId=service_id, activeAllocations=len(re.findall(r"id=\d+, user <", report)),
                              receivedBytes=sum(int(rx) for rx, _ in usage),
                              sentBytes=sum(int(tx) for _, tx in usage))))
    else:
        with database() as db:
            for row in db.execute("SELECT user,label,revoked,expires FROM devices ORDER BY label"):
                print(*row, sep="\t")


if __name__ == "__main__":
    main()
