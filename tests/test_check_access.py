"""Regression checks for grant and lease data accepted by the diagnostic CLI."""
import io
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

import relay
from check_access import device_ice_server, grant_endpoint, read_object


class DeviceAccessTests(unittest.TestCase):
    def setUp(self):
        self.host = "relay.example.com"
        self.service_id = str(uuid.uuid4())
        self.grant = dict(version=1, name="Example relay", token="x" * 43,
                          serviceId=self.service_id, endpoint=f"https://{self.host}:8443")
        self.urls = [f"turn:{self.host}:3478?transport=udp", f"turns:{self.host}:5349?transport=tcp"]
        self.server = dict(urls=self.urls, username="test-user", credential="test-value")
        self.lease = dict(serviceId=self.service_id, expiresAt=1100, iceServers=[self.server])

    def test_grant_requires_importable_device_fields(self):
        self.assertEqual(grant_endpoint(self.grant).hostname, self.host)
        for field, invalid in (("version", True), ("serviceId", "x"), ("name", ""), ("name", "a" * 121),
                               ("token", ""), ("endpoint", "https://@relay.example.com")):
            with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                grant_endpoint({**self.grant, field: invalid})

    def test_long_domain_issues_an_importable_grant_with_its_full_endpoint(self):
        host = ".".join(["a" * 60, "b" * 60, "example", "com"])
        with tempfile.TemporaryDirectory() as directory, patch.object(relay, "ROOT", Path(directory)):
            with relay.database() as db:
                db.executescript(relay.SCHEMA)
                db.execute("INSERT INTO service(id,uuid) VALUES(1,?)", (self.service_id,))
            with patch.dict(relay.os.environ, RELAY_DOMAIN=host):
                grant = relay.grant("test device")
        self.assertEqual(grant_endpoint(grant).hostname, host)
        self.assertEqual(grant["endpoint"], f"https://{host}:8443")

    @patch("check_access.time.time", return_value=1000)
    def test_single_device_lease_matches_v1_protocol(self, _clock):
        self.assertEqual(device_ice_server(self.lease, self.service_id, self.host), self.server)
        for changes in ({"serviceId": "different"}, {"expiresAt": 1010}, {"expiresAt": None},
                        {"sessionId": "bound"}, {"peerAccessId": "other-device"}, {"iceServers": []},
                        {"iceServers": [self.server, self.server]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                device_ice_server({**self.lease, **changes}, self.service_id, self.host)

    @patch("check_access.time.time", return_value=1000)
    def test_rejects_a_lease_the_client_would_not_use(self, _clock):
        for changes in ({"urls": [self.urls[0]]}, {"urls": [*self.urls, "stun:other.example.com"]},
                        {"urls": [self.urls[0], "turns:other.example.com:5349?transport=tcp"]},
                        {"urls": [self.urls[0], f"turns:{self.host}:443?transport=tcp"]},
                        {"username": ""}, {"credential": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                lease = {**self.lease, "iceServers": [{**self.server, **changes}]}
                device_ice_server(lease, self.service_id, self.host)

    def test_oversize_json_is_rejected_even_if_the_json_is_complete(self):
        with self.assertRaises(ValueError):
            read_object(io.BytesIO(b"{}" + b" " * 16383))
