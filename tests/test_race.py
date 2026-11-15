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

    async def test_priority_wins_alone_without_starting_others(self):
        called = []

        async def attempt(name):
            called.append(name)
            return name

        result = await first_success(("other", "preferred"), attempt, priority="preferred", grace=1)
        self.assertEqual(result, "preferred")
        self.assertEqual(called, ["preferred"])

    async def test_priority_failure_falls_back_to_others(self):
        called = []

        async def attempt(name):
            called.append(name)
            if name == "preferred":
                return None
            return name

        result = await first_success(("preferred", "other"), attempt, priority="preferred",
                                     grace=1, stagger=0)
        self.assertEqual(result, "other")
        self.assertEqual(called, ["preferred", "other"])

    async def test_slow_priority_is_hedged(self):
        cancelled = asyncio.Event()

        async def attempt(name):
            if name == "preferred":
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            await asyncio.sleep(0.02)
            return name

        result = await first_success(("preferred", "fast"), attempt, priority="preferred",
                                     grace=0.01, stagger=0)
        self.assertEqual(result, "fast")
        self.assertTrue(cancelled.is_set())

    async def test_slow_priority_still_wins_when_others_fail(self):
        async def attempt(name):
            if name == "preferred":
                await asyncio.sleep(0.05)
                return "preferred"
            return None

        result = await first_success(("preferred", "bad"), attempt, priority="preferred",
                                     grace=0.01, stagger=0)
        self.assertEqual(result, "preferred")


if __name__ == "__main__":
    unittest.main()
