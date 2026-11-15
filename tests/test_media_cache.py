import asyncio
import tempfile
import unittest
from pathlib import Path

from media_bot.media_cache import MediaCache


class MediaCacheTests(unittest.TestCase):
    def test_put_get_copy_and_ttl_cleanup(self):
        async def run():
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                cache = MediaCache(root / "cache", ttl=60, max_bytes=1024 * 1024)
                src_dir = root / "src"
                src_dir.mkdir()
                src = src_dir / "a.mp4"
                src.write_bytes(b"video")
                entry = await asyncio.to_thread(cache.put, "instagram", "https://example.com/p/1", False, "indown", [src])
                self.assertIsNotNone(entry)
                got = await asyncio.to_thread(cache.get, "instagram", "https://example.com/p/1", False)
                self.assertIsNotNone(got)
                out = root / "out"
                copied = await asyncio.to_thread(cache.copy_to, got, out)
                self.assertEqual(len(copied), 1)
                self.assertEqual(copied[0].read_bytes(), b"video")

                # expired
                cache.ttl = 0
                self.assertIsNone(await asyncio.to_thread(cache.get, "instagram", "https://example.com/p/1", False))

        asyncio.run(run())

    def test_singleflight(self):
        async def run():
            cache = MediaCache(None)
            cache.configure(Path(tempfile.mkdtemp()), ttl=60)
            leader, fut = await cache.singleflight_start("k")
            self.assertTrue(leader)
            leader2, fut2 = await cache.singleflight_start("k")
            self.assertFalse(leader2)
            await cache.singleflight_finish("k", "ok")
            self.assertEqual(await fut2, "ok")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
