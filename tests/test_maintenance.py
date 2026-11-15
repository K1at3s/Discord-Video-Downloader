import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_bot.maintenance import Maintenance, env_enabled


class MaintenanceTests(unittest.TestCase):
    def test_default_is_off(self):
        maintenance = Maintenance()
        maintenance.configure(None)
        self.assertFalse(maintenance.active)
        maintenance.set(True)
        self.assertTrue(maintenance.active)
        maintenance.set(False)
        self.assertFalse(maintenance.active)

    def test_state_persists_between_instances(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "maintenance.json"
            first = Maintenance()
            first.configure(path, enabled=False)
            first.set(True)

            second = Maintenance()
            second.configure(path)
            self.assertTrue(second.active)

            second.set(False)
            third = Maintenance()
            third.configure(path)
            self.assertFalse(third.active)

    def test_explicit_enabled_overrides_stored_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "maintenance.json"
            first = Maintenance()
            first.configure(path, enabled=False)
            first.set(True)

            second = Maintenance()
            second.configure(path, enabled=False)
            self.assertFalse(second.active)

    def test_custom_message_is_used(self):
        maintenance = Maintenance()
        maintenance.configure(None, message="Kısa bir bakım molası.")
        self.assertEqual(maintenance.message, "Kısa bir bakım molası.")

    def test_env_flag_parsing(self):
        with patch.dict(os.environ, {"MAINTENANCE_MODE": "1"}):
            self.assertTrue(env_enabled())
        with patch.dict(os.environ, {"MAINTENANCE_MODE": "off"}):
            self.assertFalse(env_enabled())
        with patch.dict(os.environ, {"MAINTENANCE_MODE": ""}):
            self.assertIsNone(env_enabled())


if __name__ == "__main__":
    unittest.main()
