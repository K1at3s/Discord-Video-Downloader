import tempfile
import time
import unittest
from pathlib import Path

from media_bot.card_cache import CardCache


class CardCacheTests(unittest.TestCase):
    def test_disabled_cache_is_a_noop(self):
        cache = CardCache()
        self.assertFalse(cache.enabled)
        self.assertIsNone(cache.put("x", "1", [Path("missing.png")]))
        self.assertIsNone(cache.get("x", "1"))
        self.assertEqual(cache.clear(), 0)

    def test_put_get_copy_and_clear(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = CardCache(root / "cards", ttl=60, max_bytes=1024 * 1024)
            source = root / "card.png"
            source.write_bytes(b"PNG")
            entry = cache.put("x", "123", [source], "v1")
            self.assertIsNotNone(entry)
            self.assertEqual(cache.stats()["entries"], 1)

            got = cache.get("x", "123", "v1")
            self.assertIsNotNone(got)
            copied = cache.copy_to(got, root / "out")
            self.assertEqual(len(copied), 1)
            self.assertEqual(copied[0].read_bytes(), b"PNG")

            self.assertEqual(cache.stats()["hits"], 1)
            self.assertEqual(cache.stats()["files"], 1)
            self.assertEqual(cache.clear(), 1)
            self.assertIsNone(cache.get("x", "123", "v1"))

    def test_expired_entry_is_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = CardCache(root / "cards", ttl=1, max_bytes=1024 * 1024)
            source = root / "card.png"
            source.write_bytes(b"PNG")
            entry = cache.put("reddit", "abc", [source])
            self.assertIsNotNone(entry)
            stale = time.time() - 10
            (entry.path / "meta.json").write_text(
                '{"created_at": %s, "files": ["card.png"]}' % stale,
                encoding="utf-8",
            )
            self.assertIsNone(cache.get("reddit", "abc"))
            self.assertFalse(entry.path.exists())

    def test_only_image_files_are_stored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = CardCache(root / "cards")
            text = root / "notes.txt"
            text.write_text("ignore", encoding="utf-8")
            self.assertIsNone(cache.put("x", "1", [text]))
