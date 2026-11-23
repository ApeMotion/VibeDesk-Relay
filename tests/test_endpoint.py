import socket
import unittest
from unittest.mock import patch

from scripts.endpoint import resolve


class InstallerEndpointTests(unittest.TestCase):
    @patch("scripts.endpoint.socket.getaddrinfo")
    def test_ip_and_custom_domain(self, dns):
        public = "192.0.0.9"
        dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (public, 0))]
        self.assertEqual(resolve(public), ("192-0-0-9.sslip.io", public))
        dns.assert_called_with("192-0-0-9.sslip.io", None, type=socket.SOCK_STREAM)
        self.assertEqual(resolve("Relay.Example.com."), ("relay.example.com", public))
        with self.assertRaises(ValueError):
            resolve("192.0.0.10")

    @patch("scripts.endpoint.socket.getaddrinfo")
    def test_invalid_and_ambiguous_dns(self, dns):
        with self.assertRaises(ValueError):
            resolve("https://relay.example.com")
        dns.assert_not_called()
        for records in (["192.168.1.2"], ["192.0.0.9", "2001:db8::1"]):
            dns.return_value = [(None, None, None, "", (ip, 0)) for ip in records]
            with self.subTest(records=records), self.assertRaises(ValueError):
                resolve("relay.example.com")
