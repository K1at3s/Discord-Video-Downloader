import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from media_bot.links import PostLink
from media_bot.maintenance import MAINTENANCE
from media_bot.retry_view import RetryView


def make_view(owner_id=1, author_id=1):
    message = SimpleNamespace(
        author=SimpleNamespace(id=author_id, name="tester"),
        guild=None,
        channel=None,
    )
    return RetryView(bot=SimpleNamespace(owner_id=owner_id), source_message=message,
                     link=PostLink("tiktok", "https://www.tiktok.com/@a/video/1", "1"))


class RetryViewTests(unittest.IsolatedAsyncioTestCase):
    def test_has_retry_button(self):
        view = make_view()
        self.assertEqual(len(view.children), 1)
        self.assertEqual(view.children[0].label, "Tekrar dene")

    async def test_other_user_cannot_retry(self):
        view = make_view()
        send = AsyncMock()
        interaction = SimpleNamespace(user=SimpleNamespace(id=2),
                                      response=SimpleNamespace(send_message=send))
        self.assertFalse(await view.interaction_check(interaction))
        send.assert_awaited_once()

    async def test_maintenance_blocks_owner_except_self(self):
        view = make_view(owner_id=1, author_id=2)
        MAINTENANCE.configure(None, enabled=True, message="Bakım var.")
        try:
            send = AsyncMock()
            blocked = SimpleNamespace(user=SimpleNamespace(id=2),
                                      response=SimpleNamespace(send_message=send))
            self.assertFalse(await view.interaction_check(blocked))
            self.assertEqual(send.await_args.args[0], "Bakım var.")

            owner_send = AsyncMock()
            owner = SimpleNamespace(user=SimpleNamespace(id=1),
                                    response=SimpleNamespace(send_message=owner_send))
            self.assertTrue(await view.interaction_check(owner))
            owner_send.assert_not_awaited()
        finally:
            MAINTENANCE.configure(None, enabled=False)

    def test_set_disabled_toggles_button(self):
        view = make_view()
        view._set_disabled(True)
        self.assertTrue(all(item.disabled for item in view.children))
        view._set_disabled(False)
        self.assertFalse(any(item.disabled for item in view.children))


if __name__ == "__main__":
    unittest.main()
