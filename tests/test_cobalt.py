import unittest

from media_bot.cobalt import _extension, _same_instance


class CobaltSafetyTests(unittest.TestCase):
    def test_tunnel_must_stay_on_configured_instance(self):
        base = "http://127.0.0.1:9000/"
        self.assertTrue(_same_instance("http://127.0.0.1:9000/tunnel?id=123", base))
        self.assertFalse(_same_instance("http://127.0.0.1:9999/tunnel", base))
        self.assertFalse(_same_instance("http://127.0.0.1.evil.test/tunnel", base))
        self.assertFalse(_same_instance("https://127.0.0.1:9000/tunnel", base))

    def test_output_extension_is_constrained(self):
        self.assertEqual(_extension("../../foo.exe", "photo"), ".jpg")
        self.assertEqual(_extension("clip.mp4", "video"), ".mp4")
        self.assertEqual(_extension("audio.mp3", "video"), ".mp3")


if __name__ == "__main__":
    unittest.main()
