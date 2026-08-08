from __future__ import annotations

import unittest

from services.bridge.hia_bridge.scene_writer import SceneWriterOwnership


class SceneWriterOwnershipTests(unittest.TestCase):
    def test_owner_uses_exact_thread_turn_and_waits_for_exact_hia_item(self) -> None:
        writer = SceneWriterOwnership()
        reservation = writer.reserve("project", "project-a")
        owner = writer.bind(
            reservation, "thread-execution", "turn-execution"
        )

        self.assertEqual("thread-execution", writer.snapshot()["thread_id"])
        self.assertEqual("turn-execution", writer.snapshot()["turn_id"])
        writer.hia_started(owner, "hia-item-a")
        self.assertFalse(writer.turn_terminal(owner))
        self.assertTrue(writer.retained_after_terminal(owner))
        self.assertTrue(writer.hia_finished(owner, "hia-item-a"))
        self.assertIsNone(writer.snapshot()["owner"])

    def test_anonymous_hia_item_releases_after_matching_completion(self) -> None:
        writer = SceneWriterOwnership()
        reservation = writer.reserve("ordinary", "thread-a")
        owner = writer.bind(reservation, "thread-a", "turn-a")

        writer.hia_anonymous_started(owner)
        self.assertFalse(writer.turn_terminal(owner))
        self.assertEqual(1, writer.snapshot()["anonymous_hia_items"])
        self.assertTrue(writer.hia_anonymous_finished(owner))
        self.assertIsNone(writer.snapshot()["owner"])

    def test_unmatched_anonymous_completion_never_goes_negative(self) -> None:
        writer = SceneWriterOwnership()
        reservation = writer.reserve("ordinary", "thread-a")
        owner = writer.bind(reservation, "thread-a", "turn-a")

        self.assertFalse(writer.hia_anonymous_finished(owner))
        self.assertEqual(0, writer.snapshot()["anonymous_hia_items"])
        self.assertTrue(writer.turn_terminal(owner))


if __name__ == "__main__":
    unittest.main()
