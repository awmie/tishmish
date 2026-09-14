"""Tests for the app-owned playback queue.

stdlib unittest only: the repo has no test dependencies and this file must run
with nothing installed beyond the venv, e.g.

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/maficx/m \\
      venv311/bin/python -m unittest discover -s tests -v

Why a hand-written queue is allowed to exist at all: every historical queue bug
in this bot came from reaching into the backend's private deque
(`del vc.queue._queue[i]`, `vc.queue._wakeup_next()`) or from the get_wait /
put_wait handshake those private edits silently bypassed. This module has no
waiter to wake and no private state to reach into, which is the only sense in
which it is safer than what it replaces. It is also the reason these tests are
written against positions: the old code numbered the list for the member in one
place and indexed it in another.
"""
import sys
import unittest

sys.path.insert(0, "/Users/awmie/tishmish")

from app import QueueFull, QueueEmpty, TrackQueue  # noqa: E402


class FakeTrack:
    """Stands in for mafic.Track: only identity and a title matter here."""

    __slots__ = ("title",)

    def __init__(self, title):
        self.title = title

    def __repr__(self):
        return f"<{self.title}>"


def make(n, prefix="t"):
    return [FakeTrack(f"{prefix}{i}") for i in range(1, n + 1)]


class TestBasics(unittest.TestCase):
    def test_starts_empty(self):
        q = TrackQueue()
        self.assertTrue(q.is_empty)
        self.assertEqual(len(q), 0)
        self.assertIsNone(q.peek())

    def test_fifo_order(self):
        a, b, c = make(3)
        q = TrackQueue()
        q.append(a), q.append(b), q.append(c)
        self.assertEqual([q.advance(), q.advance(), q.advance()], [a, b, c])
        self.assertIsNone(q.advance())

    def test_len_iter_contains(self):
        items = make(3)
        q = TrackQueue(*items)
        self.assertEqual(len(q), 3)
        self.assertEqual(list(q), items)
        self.assertIn(items[1], q)
        self.assertNotIn(FakeTrack("other"), q)

    def test_empty_iteration_is_not_an_error(self):
        # The bug this guards: building an array/summary from an empty queue used
        # to raise (UFuncTypeError) rather than report "nothing queued".
        q = TrackQueue()
        self.assertEqual(list(q), [])
        self.assertEqual(q.display_rows(), [])
        self.assertEqual(q.titles(), [])


class TestCapacity(unittest.TestCase):
    def test_append_is_capped_and_reports_it(self):
        q = TrackQueue(max_size=3)
        a, b, c, d = make(4)
        for x in (a, b, c):
            self.assertTrue(q.append(x))
        self.assertTrue(q.is_full)
        with self.assertRaises(QueueFull):
            q.append(d)
        self.assertEqual(len(q), 3, "a rejected append must not grow the queue")

    def test_zero_and_negative_max_size_rejected_at_construction(self):
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                TrackQueue(max_size=bad)

    def test_remaining_counts_down(self):
        q = TrackQueue(max_size=5)
        q.extend(make(2))
        self.assertEqual(q.remaining, 3)


class TestPositionsAreOneBased(unittest.TestCase):
    """The class of bug where /queue shows one number and /del accepts another."""

    def test_display_positions_match_removal(self):
        items = make(4)
        q = TrackQueue(*items)
        rows = q.display_rows()
        self.assertEqual([r.position for r in rows], [1, 2, 3, 4])
        for row in rows:
            self.assertIs(q.get_at(row.position), row.track)

    def test_remove_position_leaves_the_rest_in_order(self):
        a, b, c, d = make(4)
        q = TrackQueue(a, b, c, d)
        self.assertIs(q.remove_at(2), b)
        self.assertEqual([r.track for r in q.display_rows()], [a, c, d])
        self.assertEqual([r.position for r in q.display_rows()], [1, 2, 3],
                         "positions must be recomputed, not remembered")

    def test_out_of_range_and_zero_are_refused_not_wrapped(self):
        q = TrackQueue(*make(2))
        for bad in (0, -1, 3, 99):
            with self.assertRaises(IndexError):
                q.remove_at(bad)
            with self.assertRaises(IndexError):
                q.get_at(bad)

    def test_python_negative_index_cannot_bypass_the_guard(self):
        q = TrackQueue(*make(2))
        with self.assertRaises(IndexError):
            q.remove_at(-1)


class TestMove(unittest.TestCase):
    def test_move_to_and_from_same_position_is_a_no_op(self):
        items = make(3)
        q = TrackQueue(*items)
        q.move(2, 2)
        self.assertEqual(list(q), items)

    def test_move_down_reorders_without_losing_or_duplicating(self):
        a, b, c, d = make(4)
        q = TrackQueue(a, b, c, d)
        q.move(1, 3)
        self.assertEqual(list(q), [b, c, a, d])
        self.assertEqual(len(q), 4)

    def test_move_up(self):
        a, b, c, d = make(4)
        q = TrackQueue(a, b, c, d)
        q.move(4, 1)
        self.assertEqual(list(q), [d, a, b, c])

    def test_move_validates_both_ends(self):
        q = TrackQueue(*make(3))
        for src, dst in ((0, 1), (1, 0), (4, 1), (1, 4), (-2, 1)):
            with self.assertRaises(IndexError):
                q.move(src, dst)
        self.assertEqual(len(q), 3)

    def test_move_preserves_identity_not_equality(self):
        # Two entries with the same title must not be conflated: the old code
        # used deque.remove(), which deletes the FIRST EQUAL element, and the
        # backend's Track had no __eq__ so equality was identity - a distinction
        # that silently broke under /loopqueue, which re-queued the same object.
        a1, a2 = FakeTrack("same"), FakeTrack("same")
        q = TrackQueue(a1, a2)
        q.move(2, 1)
        self.assertIs(q.get_at(1), a2)
        self.assertIs(q.get_at(2), a1)


class TestShuffle(unittest.TestCase):
    def test_shuffle_preserves_the_multiset(self):
        items = make(20)
        q = TrackQueue(*items)
        q.shuffle()
        self.assertEqual(sorted(t.title for t in q), sorted(t.title for t in items))
        self.assertEqual(len(q), 20)

    def test_shuffle_on_tiny_queue_is_safe(self):
        for n in (0, 1, 2):
            q = TrackQueue(*make(n))
            q.shuffle()
            self.assertEqual(len(q), n)


class TestAdvanceAndLooping(unittest.TestCase):
    def test_advance_drains_by_default(self):
        a, b = make(2)
        q = TrackQueue(a, b)
        self.assertIs(q.advance(), a)
        self.assertIs(q.advance(), b)
        self.assertIsNone(q.advance())

    def test_loop_queue_cycles_instead_of_emptying(self):
        a, b, c = make(3)
        q = TrackQueue(a, b, c)
        first = q.advance(finished=a, loop_queue=True)
        self.assertIs(first, b)
        self.assertEqual([t.title for t in q.display_rows()], ["t3", "t1"],
                         "finished track rotates to the back")
        self.assertEqual(len(q), 2, "cycling must not grow the queue")

    def test_loop_queue_with_one_track_keeps_playing_it(self):
        a = FakeTrack("only")
        q = TrackQueue(a)
        for _ in range(3):
            nxt = q.advance(finished=a, loop_queue=True)
            self.assertIs(nxt, a, "a single-item looping queue must not go empty")

    def test_finished_not_in_queue_is_still_consistent(self):
        # `finished` may be absent: it is the track currently on air, which is
        # not in the queue at all, and /del can also drop a queued entry. Looping
        # the queue must then put it back once, not lose it and not double it.
        a, b, c = make(3)
        q = TrackQueue(b, c)
        self.assertIs(q.advance(finished=a, loop_queue=True), b)
        self.assertEqual([t.title for t in q], ["t3", "t1"])
        self.assertEqual(len(q), 2)

    def test_stop_looping_leaves_no_duplicate_of_the_current_track(self):
        # The specific nextwave defect: /loopqueue start injected the playing
        # source and /loopqueue stop compared an AudioSource to a Track, so the
        # copy was never removed and played again after the queue drained.
        a, b = make(2)
        q = TrackQueue(a, b)
        q.advance(finished=a, loop_queue=True)   # a rotates to the back
        self.assertEqual([t.title for t in q], ["t1"])
        self.assertIs(q.advance(finished=b, loop_queue=False), a)
        self.assertTrue(q.is_empty)


class TestClear(unittest.TestCase):
    def test_clear_empties_and_resets(self):
        q = TrackQueue(*make(5))
        q.clear()
        self.assertTrue(q.is_empty)
        self.assertIsNone(q.peek())
        self.assertEqual(q.remaining, q.max_size)

    def test_peek_does_not_consume(self):
        a, b = make(2)
        q = TrackQueue(a, b)
        self.assertIs(q.peek(), a)
        self.assertIs(q.peek(), a)
        self.assertEqual(len(q), 2)


class TestTitlesForTheAISeed(unittest.TestCase):
    def test_titles_are_strings_and_bounded(self):
        q = TrackQueue(*make(50))
        titles = q.titles(limit=10)
        self.assertEqual(len(titles), 10)
        self.assertTrue(all(isinstance(t, str) for t in titles))
        self.assertEqual(titles[0], "t1")

    def test_titles_without_limit_returns_all(self):
        q = TrackQueue(*make(13))
        self.assertEqual(len(q.titles()), 13)

    def test_repr_of_the_queue_is_not_the_queue(self):
        # Guards the /predict seed bug: f"{vc.queue}" used to interpolate the
        # object. str() of this queue is human-readable and lists titles.
        q = TrackQueue(*make(2))
        self.assertNotIn("0x", str(q))
        self.assertIn("t1", str(q))


if __name__ == "__main__":
    unittest.main()
