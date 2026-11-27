import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock


from media_bot.cancel_view import CancelView


class CancelViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_interaction_check_and_cancel(self):
        task = asyncio.create_task(asyncio.sleep(60))
        self.addCleanup(task.cancel)
        view = CancelView(task=task, owner_id=10, bot_owner_id=20, locale="tr")

        interaction = SimpleNamespace(user=SimpleNamespace(id=999), response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()), message=SimpleNamespace(edit=AsyncMock()))
        allowed = await view.interaction_check(interaction)
        self.assertFalse(allowed)
        interaction.response.send_message.assert_awaited()

        interaction.user.id = 10
        self.assertTrue(await view.interaction_check(interaction))

        await view.cancel(interaction)
        self.assertTrue(view.cancelled)
        interaction.response.defer.assert_awaited()
        interaction.message.edit.assert_awaited()
        self.assertTrue(task.cancelled() or task.cancelling())

    async def test_cancel_too_late(self):
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        view = CancelView(task=task, owner_id=10, locale="tr")
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
        await view.cancel(interaction)
        interaction.response.send_message.assert_awaited()