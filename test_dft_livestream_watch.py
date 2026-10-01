"""Offline regression tests: dates, DST, precision, dedup and access refusals."""
from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests
import dft_livestream_watch as w

DAY = date(2026, 10, 1)
SOURCE = "https://www.dftsf.com/"


def page(body, heading="October 1, 2026"):
    return f"<html><h1>Downtown First Thursdays</h1><h2>{heading}</h2><p>Free monthly street party with music, community, local artists and dancing in downtown San Francisco. All ages welcome at our first Thursday event.</p>{body}</html>"


def hits(body, heading="October 1, 2026"):
    return w.extract(page(body, heading), SOURCE, DAY)[0]


def response(status, text, headers=None):
    value = requests.Response()
    value.status_code = status
    value._content = text.encode()
    value.headers.update(headers or {})
    return value


class CalendarTests(unittest.TestCase):
    def test_event_evening_is_friday_in_berlin(self):
        self.assertEqual(w.event_day(datetime.fromisoformat("2026-10-02T05:00:00+02:00")), DAY)

    def test_november_winter_time(self):
        self.assertEqual(w.event_day(datetime.fromisoformat("2026-11-06T02:00:00+01:00")), date(2026, 11, 5))

    def test_second_thursday_is_not_event_day(self):
        self.assertIsNone(w.event_day(datetime.fromisoformat("2026-10-08T17:00:00-07:00")))

    def test_end_of_local_thursday(self):
        self.assertEqual(w.event_day(datetime.fromisoformat("2026-10-02T06:59:00+00:00")), DAY)
        self.assertIsNone(w.event_day(datetime.fromisoformat("2026-10-02T07:00:00+00:00")))

    def test_first_thursday_can_be_day_seven(self):
        self.assertEqual(w.event_day(datetime.fromisoformat("2027-01-08T06:00:00+00:00")), date(2027, 1, 7))

    def test_naive_date_rejected(self):
        with self.assertRaises(ValueError):
            w.event_day(datetime(2026, 10, 1))

    def test_next_event(self):
        self.assertEqual(w.next_event(datetime.fromisoformat("2026-10-10T12:00:00+00:00")), date(2026, 11, 5))


class ExtractionTests(unittest.TestCase):
    def test_new_dated_video_is_not_suppressed_on_first_run(self):
        found = hits('<p>October 1, 2026: <a href="https://youtu.be/abc123xyz01">Watch live</a></p>')
        self.assertEqual(len(w.pending_hits(found, {"schema": 1, "sent": {}}, DAY)), 1)
        self.assertEqual(found[0]["url"], "https://www.youtube.com/watch?v=abc123xyz01")

    def test_current_page_heading_dates_announcement(self):
        self.assertEqual(len(hits('<p><a href="https://twitch.tv/dft">Our livestream</a></p>')), 1)

    def test_live_music_is_not_video(self):
        self.assertEqual(hits('<p>Live music with great DJs! <a href="https://youtube.com/watch?v=abc123xyz01">Video</a></p>'), [])

    def test_radio_is_not_video(self):
        self.assertEqual(hits('<p>Live broadcast dance party on 91.7 FM. <a href="https://kalw.org/live">Listen live</a></p>'), [])

    def test_generic_social_icon_is_not_live(self):
        self.assertEqual(hits('<p><a href="https://instagram.com/dftsanfrancisco/">Instagram</a></p>'), [])

    def test_old_dated_video_is_rejected(self):
        self.assertEqual(hits('<p>October 1, 2025 <a href="https://youtu.be/abc123xyz01">Watch live</a></p>'), [])

    def test_stale_event_page_is_rejected(self):
        self.assertEqual(hits('<p><a href="https://twitch.tv/dft">Livestream</a></p>', 'August 6, 2026'), [])

    def test_undated_page_is_not_sufficient(self):
        self.assertEqual(hits('<p><a href="https://twitch.tv/dft">Livestream</a></p>', 'Welcome'), [])

    def test_recap_is_rejected(self):
        self.assertEqual(hits('<p>October 1, 2026 recap: <a href="https://youtu.be/abc123xyz01">Livestream replay</a></p>'), [])

    def test_specific_tiktok_live(self):
        self.assertEqual(len(hits('<p>October 1, 2026 <a href="https://www.tiktok.com/@dft/live">Watch live</a></p>')), 1)

    def test_footer_is_not_a_stream_announcement(self):
        self.assertEqual(hits('<footer>October 1, 2026 <a href="https://twitch.tv/unrelated">Watch live</a></footer>'), [])

    def test_bad_page_is_failure_not_quiet(self):
        with self.assertRaises(RuntimeError):
            w.extract('<html>Verify you are human</html>', SOURCE, DAY)

    def test_dates(self):
        self.assertIn(DAY, w.dates_in('Oct. 1st, 2026 and 2026-10-01', 2026))
        self.assertEqual(w.dates_in('2026-99-88 and February 31, 2026', 2026), set())

    def test_duplicate_url_formats(self):
        a = w.canonical('https://youtu.be/abc123xyz01?si=tracking')
        b = w.canonical('https://www.youtube.com/live/abc123xyz01?utm_source=x')
        self.assertEqual(a, b)
        found = [{"url": a}, {"url": b}]
        self.assertEqual(len(w.pending_hits(found, {"sent": {}}, DAY)), 1)
        self.assertEqual(w.pending_hits(found, {"sent": {DAY.isoformat(): {a: {}}}}, DAY), [])

    def test_invalid_state_does_not_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text('{"sent": []}')
            with self.assertRaises(ValueError):
                w.load_state(path)


class AccessTests(unittest.TestCase):
    def test_robots_disallow_is_respected(self):
        reader = w.PublicReader()
        with patch.object(reader, 'request', return_value=response(200, 'User-agent: *\nDisallow: /')) as request:
            with self.assertRaisesRegex(RuntimeError, 'disallows'):
                reader.read(SOURCE)
            self.assertEqual(request.call_count, 1)

    def test_robots_block_is_not_bypassed(self):
        reader = w.PublicReader()
        with patch.object(reader, 'request', return_value=response(403, 'Forbidden')) as request:
            with self.assertRaisesRegex(RuntimeError, 'policy'):
                reader.read(SOURCE)
            self.assertEqual(request.call_count, 1)

    def test_same_site_robots_redirect(self):
        reader = w.PublicReader()
        responses = [response(301, '', {'Location': 'https://dftsf.com/robots.txt'}), response(200, 'User-agent: *\nAllow: /'), response(200, page(''), {'content-type': 'text/html'})]
        with patch.object(reader, 'request', side_effect=responses):
            self.assertIn('Downtown First Thursdays', reader.read(SOURCE))


if __name__ == '__main__':
    unittest.main()
