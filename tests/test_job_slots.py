import asyncio
import unittest

from media_bot.job_slots import JobSlots


class JobSlotsTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_user_waits_while_other_user_can_run(self):
        slots = JobSlots(asyncio.Semaphore(2))
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        other_started = asyncio.Event()
        queued = []

        async def notify_second():
            queued.append("same user")

        async def first():
            async with slots.slot(1, lambda: asyncio.sleep(0)):
                first_started.set()
                await release_first.wait()

        async def second():
            async with slots.slot(1, notify_second):
                second_started.set()

        async def other():
            async with slots.slot(2, lambda: asyncio.sleep(0)):
                other_started.set()

        first_task = asyncio.create_task(first())
        await first_started.wait()
        second_task = asyncio.create_task(second())
        other_task = asyncio.create_task(other())
        await asyncio.wait_for(other_started.wait(), 1)
        self.assertEqual(queued, ["same user"])
        self.assertFalse(second_started.is_set())
        release_first.set()
        await asyncio.gather(first_task, second_task, other_task)
        self.assertTrue(second_started.is_set())
        self.assertEqual(slots._users, {})

    async def test_global_limit_announces_queue_and_cancel_releases_user(self):
        slots = JobSlots(asyncio.Semaphore(1))
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        queued = asyncio.Event()

        async def first():
            async with slots.slot(1, lambda: asyncio.sleep(0)):
                first_started.set()
                await release_first.wait()

        first_task = asyncio.create_task(first())
        await first_started.wait()

        async def on_wait():
            queued.set()

        waiting_task = asyncio.create_task(slots.slot(2, on_wait).__aenter__())
        await asyncio.wait_for(queued.wait(), 1)
        waiting_task.cancel()
        await asyncio.gather(waiting_task, return_exceptions=True)
        release_first.set()
        await first_task
        self.assertEqual(slots._users, {})


if __name__ == "__main__":
    unittest.main()
