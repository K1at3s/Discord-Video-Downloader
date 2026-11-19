import unittest

from media_bot.errors import classify, user_message


class ErrorMessageTests(unittest.TestCase):
    def test_classifies_common_failures(self):
        cases = {
            "login required": "error_private",
            "confirm you're not a bot": "error_bot_check",
            "HTTP 429 too many requests": "error_rate_limit",
            "operation timed out": "error_timeout",
            "file is too large": "error_too_large",
            "post not found": "error_not_found",
            "unsupported link": "error_unsupported",
            "something odd": "error_generic",
        }
        for detail, expected in cases.items():
            self.assertEqual(classify(detail), expected, detail)

    def test_messages_are_localized(self):
        self.assertIn("özel", user_message("private account", "tr"))
        self.assertIn("private", user_message("private account", "en"))
        self.assertIn("Discord", user_message("too large", "en"))
        self.assertEqual(user_message("unknown", "tr"), user_message("unknown", "tr"))

    def test_platform_is_injected_for_missing_media(self):
        text = user_message("no media", "en", platform="TIKTOK")
        self.assertIn("TIKTOK", text)
