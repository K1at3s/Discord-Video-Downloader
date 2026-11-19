import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from media_bot import i18n
from media_bot.guild_settings import SETTINGS, GuildSettingsStore


class LocaleSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GuildSettingsStore(Path(self.temp.name) / "settings.sqlite3")
        self.previous = SETTINGS.path
        SETTINGS.configure(self.store.path)

    def tearDown(self):
        SETTINGS.configure(self.previous)
        self.temp.cleanup()

    def test_default_locale_is_turkish(self):
        self.assertEqual(self.store.get(1).locale, "tr")
        self.assertEqual(i18n.locale_for_message(
            SimpleNamespace(guild=SimpleNamespace(id=1))
        ), "tr")

    def test_locale_update_is_per_guild(self):
        self.store.update(1, locale="en")
        self.assertEqual(self.store.get(1).locale, "en")
        self.assertEqual(self.store.get(2).locale, "tr")
        message = SimpleNamespace(guild=SimpleNamespace(id=1))
        self.assertEqual(i18n.locale_for_message(message), "en")

    def test_invalid_locale_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update(1, locale="de")

    def test_settings_text_shows_language(self):
        text = i18n.settings_message(self.store.get(1), "tr", updated=False)
        self.assertIn("Dil", text)
        self.assertIn("Türkçe", text)
