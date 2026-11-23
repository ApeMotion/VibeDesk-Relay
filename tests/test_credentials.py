"""Check that TURN can authenticate while another device is renewing."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import relay


class ConcurrentCredentialsTests(unittest.TestCase):
    def test_turn_reader_sees_committed_key_during_another_write(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(relay, "ROOT", Path(directory)):
            path = Path(directory, "relay.sqlite")
            path.touch()
            relay.initialize()
            with relay.database() as writer:
                writer.execute("INSERT INTO turnusers_lt VALUES('vibedesk','device','first')")
            with relay.database() as writer:
                writer.execute("BEGIN EXCLUSIVE")
                writer.execute("UPDATE turnusers_lt SET hmackey='next' WHERE name='device'")
                reader = sqlite3.connect(path, timeout=0)
                try:
                    key, = reader.execute("SELECT hmackey FROM turnusers_lt WHERE name='device'").fetchone()
                    self.assertEqual(key, "first")
                finally:
                    reader.close()
