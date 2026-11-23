"""Run inside the disposable test container started by tests/smoke.sh."""
import concurrent.futures
import contextlib
import hashlib
import json
import os
import re
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

sys.path.insert(0, "/opt/vibedesk")
import relay
from check_access import allocated, allocation, attr, encode, exchange, receive

assert os.environ.get("RELAY_LOCAL_TEST") == "1"
tls = ssl.create_default_context(cafile=str(relay.CERTS / "fullchain.pem"))
ip = socket.gethostbyname(socket.gethostname())


def access_id(config):
    return hashlib.sha256(config["token"].encode()).hexdigest()[:32]


def issued(config, peer=None, session=None):
    headers = {"Authorization": "Bearer " + config["token"]}
    if peer:
        headers.update({"X-VibeDesk-Relay-Peer": access_id(peer), "X-VibeDesk-Relay-Session": session})
    request = urllib.request.Request(config["endpoint"] + "/v1/credentials", headers=headers)
    with urllib.request.urlopen(request, timeout=3, context=tls) as response:
        result = json.load(response)
    assert result["serviceId"] == config["serviceId"] and result["sessionId"] == session
    return result["iceServers"][0]


def sessions(name):
    return re.findall(r"id=(\d+), user <", relay.cli(["ps " + name])[0])


def keep_sending(sender, receiver, started, revoked):
    for own, other in [(sender, receiver), (receiver, sender)]:
        stream, auth, key, _ = own
        kind, _ = exchange(stream, True, 8, attr(0x12, other[3]) + auth, key)
        assert kind == 0x108
    for number in range(160):
        src, dst = (sender, receiver) if number % 2 else (receiver, sender)
        payload = struct.pack("!I", number) + os.urandom(97)
        src[0].sendall(encode(0x16, attr(0x12, dst[3]) + attr(0x13, payload), os.urandom(12)))
        kind, fields, _ = receive(dst[0], True)
        assert kind == 0x17 and fields[0x13] == payload
        if number == 8:
            started.set()
            assert revoked.wait(10)


with contextlib.ExitStack() as stack:
    configs = [relay.grant(label) for label in ("local-revoke", "local-survivor", "local-partner")]
    single = [issued(c) for c in configs]
    devices = [ice["username"].split(".")[0] for ice in single]
    for device in devices:
        stack.callback(relay.revoke, device)
    target, survivor, partner = configs
    for secure in (False, True):
        allocation(single[0], "localhost", ip, tls, secure)
    active = lambda ice: stack.enter_context(allocated(ice, "localhost", ip, tls, True))
    involved = str(uuid.uuid4())
    owned = issued(target, survivor, involved)
    remote_owned = issued(survivor, target, involved)
    target_stream = active(owned)
    active(remote_owned)
    print("Verified TURN endpoint:", target_stream[0].version(), flush=True)
    unaffected = str(uuid.uuid4())
    a = active(issued(survivor, partner, unaffected))
    b = active(issued(partner, survivor, unaffected))
    assert sessions(owned["username"]) and sessions(remote_owned["username"])
    started, revoked = threading.Event(), threading.Event()
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        traffic = pool.submit(keep_sending, a, b, started, revoked)
        assert started.wait(6)
        start = time.monotonic()
        try:
            relay.revoke(devices[0])
            for config, peer in ((target, survivor), (survivor, target)):
                try:
                    issued(config, peer, involved)
                    raise AssertionError("Revoked participant obtained a paired lease")
                except urllib.error.HTTPError as error:
                    assert error.code == 403
            for ice in [owned, remote_owned]:
                assert not sessions(ice["username"])
                try:
                    allocation(ice, "localhost", ip, tls, True)
                    raise AssertionError("Old credential established a new allocation")
                except OSError as error:
                    assert "rejected (401)" in str(error), error
            elapsed = time.monotonic() - start
            assert elapsed < 60
            print("Both owners' allocations revoked and both old credentials refused in %.2fs" % elapsed, flush=True)
        finally:
            revoked.set()
        traffic.result(10)
    print("Unaffected authorized pair exchanged 160/160 TURN/TLS messages across revocation", flush=True)

    with contextlib.ExitStack() as allocations:
        for _ in range(8):
            allocations.enter_context(allocated(single[1], "localhost", ip, tls, True))
        try:
            allocation(single[1], "localhost", ip, tls, True)
            raise AssertionError("Per-username quota exceeded")
        except OSError as error:
            assert "rejected (486)" in str(error), error
    allocation(single[1], "localhost", ip, tls, True)
    print("Allocation quota enforced; released capacity reusable", flush=True)

    # Advance the issuer's clock only. Authenticate a new TURN allocation using
    # the actual original native credential after extending the same short lease.
    original = issued(survivor, partner, unaffected)
    clock = time.time()
    with patch.object(relay.time, "time", return_value=clock + 540):
        renewed = relay.credentials(survivor["token"], access_id(partner), unaffected)
    assert renewed["expiresAt"] > clock + 600
    assert renewed["iceServers"][0] == original
    allocation(original, "localhost", ip, tls, True)
    with patch.object(relay.time, "time", return_value=clock + 1800):
        fresh = relay.credentials(survivor["token"], access_id(partner), unaffected)
    assert fresh["iceServers"][0]["username"] != original["username"]
    print("Renewed session can reallocate; expired session gets a different credential", flush=True)
