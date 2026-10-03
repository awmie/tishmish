"""Regression tests for the /loop replay path.

The bug this pins: on a natural track end, Lavalink encodes the track it reports
in the END event with the position it stopped at, and mafic hands that encoded
string through as track.id. The loop handler replayed that exact track, so every
restart began ~0.6s before the end, finished, and restarted -- an unthrottled
play-request storm against the node (observed live: 72 PATCHes in ~30s). The
replay must start the song at 0 instead.

stdlib unittest only, matching tests/test_queue.py; runs under
    venv311/bin/python -m unittest discover -s tests -v
"""
import asyncio
import sys
import unittest

sys.path.insert(0, "/Users/awmie/tishmish")

import app  # noqa: E402
from app import EndReason, GuildState, _advance_queue, on_track_end  # noqa: E402


class FakeTrack:
    __slots__ = ("id", "identifier", "title", "length")

    def __init__(self, tid, title="song"):
        self.id = tid
        self.identifier = tid
        self.title = title
        self.length = 199000

    def __repr__(self):
        return f"<{self.title}>"


class FakePlayer:
    def __init__(self):
        self.state = GuildState(1)
        self.played = []
        self.advances = []

    def is_connected(self):
        return True

    async def play(self, track, **kwargs):
        self.played.append((track, kwargs))


class FakeEvent:
    def __init__(self, player, track, reason):
        self.player = player
        self.track = track
        self.reason = reason


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestLoopReplaysFromZero(unittest.TestCase):
    def setUp(self):
        self._real_advance = app._advance_queue

    def tearDown(self):
        app._advance_queue = self._real_advance

    def _event(self, player, track, reason=EndReason.FINISHED):
        return FakeEvent(player, track, reason)

    def test_looped_track_restarts_at_zero_and_not_from_the_queue(self):
        player = FakePlayer()
        player.state.loop_track = True
        track = FakeTrack("encoded-with-end-position")

        async def no_advance(*args, **kwargs):
            player.advances.append(kwargs)
            return True

        app._advance_queue = no_advance
        run(on_track_end(self._event(player, track)))

        self.assertEqual(len(player.played), 1)
        replayed, kwargs = player.played[0]
        self.assertIs(replayed, track)
        self.assertEqual(
            kwargs.get("start_time"), 0,
            "a looped replay must start at 0; without it Lavalink starts at the "
            "end position baked into the end-event track and hot-loops",
        )
        self.assertEqual(player.advances, [], "looping must not touch the queue")

    def test_loop_off_still_advances_the_queue(self):
        player = FakePlayer()
        track = FakeTrack("ended")
        seen = {}

        async def record(player_, finished=None):
            seen["finished"] = finished
            return True

        app._advance_queue = record
        run(on_track_end(self._event(player, track)))

        self.assertEqual(player.played, [])
        self.assertIs(seen["finished"], track)


class TestAdvanceReplaysFromZero(unittest.TestCase):
    def test_rotated_track_is_played_from_the_start(self):
        # /loopqueue re-queues the finished track (again carrying its end
        # position); playing it must also pin the start to 0.
        player = FakePlayer()
        rotated = FakeTrack("finished-with-end-position")
        player.state.queue.append(rotated)

        run(_advance_queue(player, finished=rotated))

        self.assertEqual(len(player.played), 1)
        _, kwargs = player.played[0]
        self.assertEqual(kwargs.get("start_time"), 0)


if __name__ == "__main__":
    unittest.main()
