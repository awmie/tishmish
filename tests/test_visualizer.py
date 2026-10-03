"""Tests for the real-audio music visualiser.

The analysis and rendering are pure functions of (samples / position), so the
whole pipeline can be checked without yt-dlp, ffmpeg, a player or a gateway --
only numpy. What matters: the FFT produces sane bands, the frame reacts to the
position, and pause freezes the panel and resume continues.

stdlib unittest only, matching tests/test_queue.py.
"""
import asyncio
import sys
import unittest

import numpy as np

sys.path.insert(0, "/Users/awmie/tishmish")

import app  # noqa: E402
from app import (  # noqa: E402
    GuildState,
    _analyse_samples,
    _audio_url,
    _render_spectrum,
    _stop_visualizer,
    _vis_time,
    _visualizer_body,
    _visualizer_tick,
)


def synth(seconds=3.0):
    """A beat + bass + noisy hats at 8 kHz, so bands really do vary in time."""
    n = int(app._ANALYSIS_RATE * seconds)
    t = np.arange(n) / app._ANALYSIS_RATE
    beat = (np.sin(2 * np.pi * 2.0 * t) > 0.9).astype(np.float32)
    x = 0.5 * np.sin(2 * np.pi * 60 * t) * (0.4 + 0.6 * beat)
    x = x + 0.25 * np.sin(2 * np.pi * 1200 * t)
    rng = np.random.default_rng(0)
    x = x + 0.2 * rng.standard_normal(n).astype(np.float32) * beat
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


class FakeTrack:
    __slots__ = ("id", "identifier", "title", "author", "length", "uri", "source")

    def __init__(self, title="Song", length=200000, uri="https://youtu.be/x",
                 source="youtube", identifier="x"):
        self.id = "encoded-" + title
        self.identifier = identifier
        self.title = title
        self.author = "Artist"
        self.length = length
        self.uri = uri
        self.source = source


class FakePlayer:
    def __init__(self):
        self.state = GuildState(1)
        self.current = FakeTrack()
        self.paused = False
        self.position = 0


class DummyTask:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeMessage:
    def __init__(self):
        self.edits = []
        self.deleted = False

    async def edit(self, **fields):
        self.edits.append(fields)

    async def delete(self):
        self.deleted = True


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestTimeFormat(unittest.TestCase):
    def test_minutes_and_hours(self):
        self.assertEqual(_vis_time(0), "0:00")
        self.assertEqual(_vis_time(95), "1:35")
        self.assertEqual(_vis_time(3661), "1:01:01")


class TestAudioUrl(unittest.TestCase):
    def test_uses_an_http_uri(self):
        self.assertEqual(
            _audio_url(FakeTrack(uri="https://soundcloud.com/x/y")),
            "https://soundcloud.com/x/y",
        )

    def test_rebuilds_youtube_from_the_identifier(self):
        track = FakeTrack(uri=None, source="youtube", identifier="abc123")
        self.assertEqual(_audio_url(track), "https://www.youtube.com/watch?v=abc123")

    def test_unknown_source_has_no_url(self):
        self.assertIsNone(_audio_url(FakeTrack(uri=None, source="local", identifier="z")))


class TestAnalysis(unittest.TestCase):
    def test_bands_are_normalised_and_shaped(self):
        analysis = _analyse_samples(synth())
        self.assertEqual(analysis.bands.shape[1], app.VIS_BANDS)
        self.assertGreater(analysis.bands.shape[0], 1)
        self.assertGreaterEqual(float(analysis.bands.min()), 0.0)
        self.assertLessEqual(float(analysis.bands.max()), 1.0)
        self.assertAlmostEqual(float(np.percentile(analysis.bands, 99)), 1.0, delta=0.2)

    def test_bands_change_over_time(self):
        analysis = _analyse_samples(synth())
        first = analysis.bands[0]
        loudest = analysis.bands[analysis.bands.sum(axis=1).argmax()]
        self.assertFalse(np.allclose(first, loudest))

    def test_waveform_is_present(self):
        analysis = _analyse_samples(synth())
        self.assertEqual(analysis.wave.ndim, 1)
        self.assertGreater(analysis.wave.size, 100)
        self.assertEqual(analysis.wave_rate, app._ANALYSIS_WAVE_RATE)


class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = _analyse_samples(synth())

    def test_shape(self):
        rows = _render_spectrum(self.analysis, 500)
        self.assertEqual(len(rows), app.VIS_PANEL_ROWS)
        self.assertTrue(all(len(row) == app.VIS_BANDS for row in rows))

    def test_spectrum_rows_only_use_blocks(self):
        rows = _render_spectrum(self.analysis, 1500)
        allowed = set(" " + app._VIS_RAMP + "█")
        for row in rows[:2]:
            self.assertTrue(set(row) <= allowed, repr(row))

    def test_waveform_rows_only_use_tildes(self):
        rows = _render_spectrum(self.analysis, 1500)
        for row in rows[2:]:
            self.assertTrue(set(row) <= {" ", "~"}, repr(row))

    def test_frame_moves_with_position(self):
        self.assertNotEqual(
            _render_spectrum(self.analysis, 0),
            _render_spectrum(self.analysis, 1200),
        )


class TestBody(unittest.TestCase):
    def test_analysing_placeholder(self):
        handle = app._Visualizer()
        body = _visualizer_body(handle, FakePlayer())
        self.assertIn("analysing", body)

    def test_failed_placeholder(self):
        handle = app._Visualizer()
        handle.analysis_failed = True
        self.assertIn("no readable audio", _visualizer_body(handle, FakePlayer()))

    def test_ready_body_reflects_player_state(self):
        handle = app._Visualizer()
        handle.analysis = _analyse_samples(synth())
        handle.last_position = 90000
        player = FakePlayer()
        player.state.loop_track = True
        player.state.volume = 80
        body = _visualizer_body(handle, player)
        self.assertIn("Song", body)
        self.assertIn("```", body)
        self.assertIn("loop `track`", body)
        self.assertIn("vol `80%`", body)
        self.assertIn("1:30 / 3:20", body)

    def test_paused_marker(self):
        handle = app._Visualizer()
        handle.analysis = _analyse_samples(synth())
        player = FakePlayer()
        player.paused = True
        self.assertIn("paused", _visualizer_body(handle, player))


class TestPauseClock(unittest.TestCase):
    def test_playing_follows_the_position(self):
        player, handle = FakePlayer(), app._Visualizer()
        player.position = 5000
        first = _visualizer_tick(handle, player)
        self.assertEqual(handle.last_position, 5000)
        player.position = 9000
        second = _visualizer_tick(handle, player)
        self.assertEqual(handle.last_position, 9000)
        self.assertNotEqual(first, second)

    def test_pause_freezes_then_resume_continues(self):
        player, handle = FakePlayer(), app._Visualizer()
        player.position = 5000
        _visualizer_tick(handle, player)

        player.paused = True
        paused = _visualizer_tick(handle, player)
        self.assertEqual(handle.last_position, 5000, "pause must not advance")
        self.assertIn("paused", paused)
        self.assertIsNone(
            _visualizer_tick(handle, player),
            "after the paused frame the panel must be left untouched",
        )

        player.paused = False
        player.position = 8000
        self.assertIsNotNone(_visualizer_tick(handle, player))
        self.assertEqual(handle.last_position, 8000)


class TestEditAndStop(unittest.TestCase):
    def tearDown(self):
        app._VISUALIZERS.clear()

    def test_edit_uses_the_stored_message(self):
        message = FakeMessage()
        app._VISUALIZERS[3] = app._Visualizer(message=message)
        self.assertTrue(run(app._edit_visualizer(3, app._visualizer_embed("body"))))
        self.assertEqual(len(message.edits), 1)

    def test_stop_cancels_both_tasks(self):
        task, analysis_task = DummyTask(), DummyTask()
        app._VISUALIZERS[7] = app._Visualizer(task=task)
        app._VISUALIZERS[7].analysis_task = analysis_task
        self.assertTrue(run(_stop_visualizer(7)))
        self.assertTrue(task.cancelled)
        self.assertTrue(analysis_task.cancelled)
        self.assertNotIn(7, app._VISUALIZERS)

    def test_stop_when_nothing_running_is_false(self):
        self.assertFalse(run(_stop_visualizer(999)))

    def test_stop_with_delete_removes_the_panel(self):
        message = FakeMessage()
        app._VISUALIZERS[5] = app._Visualizer(message=message)
        self.assertTrue(run(_stop_visualizer(5, delete=True)))
        self.assertTrue(message.deleted, "leaving voice must delete the panel")
        self.assertEqual(message.edits, [])


if __name__ == "__main__":
    unittest.main()
