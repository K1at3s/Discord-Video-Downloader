import asyncio
import unittest

from media_bot.race import first_success


class ProviderRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_fallback_wins_and_cancels_slow_provider(self):
        cancelled = asyncio.Event()

        async def attempt(name):
            if name == "slow":
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return name

        result = await first_success(("slow", "fast"), attempt, stagger=0.01)
        self.assertEqual(result, "fast")
        self.assertTrue(cancelled.is_set())

    async def test_all_fail(self):
        async def attempt(name):
            return None

        self.assertIsNone(await first_success(("one", "two"), attempt, stagger=0))

    async def test_provider_exception_does_not_cancel_other_provider(self):
        async def attempt(name):
            if name == "broken":
                raise RuntimeError("extractor bug")
            await asyncio.sleep(0.01)
            return name

        with self.assertLogs("media_bot.race", level="ERROR"):
            result = await first_success(("broken", "working"), attempt, stagger=0)
        self.assertEqual(result, "working")


if __name__ == "__main__":
    unittest.main()
