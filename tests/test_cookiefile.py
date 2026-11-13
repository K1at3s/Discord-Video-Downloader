import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from media_bot.cookiefile import netscape_jar


class CookieFileTests(unittest.TestCase):
    def test_raw_header_is_not_handed_to_ytdlp(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.txt"
            path.write_text("csrftoken=abc; sessionid=def\n")
            self.assertIsNone(netscape_jar(path))

    def test_netscape_jar_is_kept(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.txt"
            path.write_text("# Netscape HTTP Cookie File\n.threads.com\tTRUE\t/\tTRUE\t0\tsessionid\tx\n")
            self.assertEqual(netscape_jar(path), path)

    def test_missing_file_is_ignored(self):
        self.assertIsNone(netscape_jar(Path("does-not-exist.txt")))
        self.assertIsNone(netscape_jar(None))


if __name__ == "__main__":
    unittest.main()
