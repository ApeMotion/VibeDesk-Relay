import socket
import unittest
from unittest.mock import patch

import relay
from scripts.endpoint import resolve


class InstallerEndpointTests(unittest.TestCase):
    @patch("scripts.endpoint.socket.getaddrinfo")
    def test_custom_domain(self, dns):
        public = "192.0.0.9"
        dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (public, 0))]
        for value in ("example.com", "Relay.Example.com."):
            host = value.lower().removesuffix(".")
            with self.subTest(value=value), patch.dict(relay.os.environ, RELAY_DOMAIN=value):
                self.assertEqual(resolve(value), (host, public))
                dns.assert_called_with(host, None, type=socket.SOCK_STREAM)
                self.assertEqual(relay.domain(), host)

    @patch("scripts.endpoint.socket.getaddrinfo")
    def test_install_and_production_relay_require_a_domain(self, dns):
        for value in ("192.0.0.9", "2001:db8::1", "localhost", "https://relay.example.com", "relay.example.com:8443"):
            with self.subTest(value=value), patch.dict(relay.os.environ, RELAY_DOMAIN=value, RELAY_LOCAL_TEST="0"):
                with self.assertRaises(ValueError):
                    resolve(value)
                with self.assertRaises(ValueError):
                    relay.domain()
        dns.assert_not_called()

    @patch("scripts.endpoint.socket.getaddrinfo")
    def test_invalid_and_ambiguous_dns(self, dns):
        for records in (["192.168.1.2"], ["192.0.0.9", "2001:db8::1"]):
            dns.return_value = [(None, None, None, "", (ip, 0)) for ip in records]
            with self.subTest(records=records), self.assertRaises(ValueError):
                resolve("relay.example.com")
        dns.side_effect = socket.gaierror(socket.EAI_NONAME, "Name not found")
        with self.assertRaisesRegex(ValueError, "A 记录"):
            resolve("relay.example.com")
