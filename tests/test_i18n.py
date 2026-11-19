import re
import tempfile
import unittest
from pathlib import Path

import discord

from media_bot import i18n
from media_bot.bot import MediaBot
from media_bot.bot_slash_commands import BotTranslator, _COMMAND_TRANSLATIONS


class LocalizationCoverageTests(unittest.TestCase):
    def test_every_string_has_both_locales(self):
        for key, translations in i18n._STRINGS.items():
            self.assertEqual({"tr", "en"}, set(translations), key)
            for locale, text in translations.items():
                self.assertTrue(text.strip(), f"{key}/{locale} is empty")

    def test_placeholders_match_between_locales(self):
        pattern = re.compile(r"\{(\w+)\}")
        for key, translations in i18n._STRINGS.items():
            self.assertEqual(
                set(pattern.findall(translations["tr"])),
                set(pattern.findall(translations["en"])),
                key,
            )

    def test_quality_labels_cover_both_locales(self):
        for locale in ("tr", "en"):
            labels = i18n._QUALITY_LABELS[locale]
            self.assertEqual(set(labels), {"sor", "mp3", "360", "720", "1080"}, locale)

    def test_help_text_lists_new_slash_commands(self):
        self.assertIn("/platform", i18n.help_text("tr"))
        self.assertIn("/logsil", i18n.help_text("tr"))
        self.assertIn("/log-delete", i18n.help_text("en"))
        self.assertIn("/clear-cache", i18n.help_text("en"))

    def test_slash_translations_are_non_empty(self):
        for source, translated in _COMMAND_TRANSLATIONS.items():
            self.assertTrue(source.strip(), "empty translation key")
            self.assertTrue(translated.strip(), source)


class TranslatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_english_locales_get_translation(self):
        translator = BotTranslator()

        class String:
            message = "yardim"

        self.assertEqual(
            await translator.translate(String(), discord.Locale.american_english, None),
            "help",
        )
        self.assertIsNone(await translator.translate(String(), discord.Locale.turkish, None))

    async def test_command_payload_contains_english_localizations(self):
        with tempfile.TemporaryDirectory() as temporary:
            bot = MediaBot(
                channel_ids=set(), upload_bytes=0, max_links=3, concurrency=2,
                cookie_file=None, cobalt_url=None, cobalt_key=None, gallery_rs_path=None,
                database_path=Path(temporary) / "bot.sqlite3",
            )
            try:
                command = bot.tree.get_command("yardim")
                payload = await command.get_translated_payload(bot.tree, BotTranslator())
                self.assertEqual(payload["name"], "yardim")
                self.assertEqual(payload["name_localizations"]["en-US"], "help")
                self.assertIn("en-US", payload["description_localizations"])
                self.assertIn("en-GB", payload["description_localizations"])
            finally:
                await bot.close()


if __name__ == "__main__":
    unittest.main()
