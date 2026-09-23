"""Offline regressions: no real email or network access."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import neurips_openreview_watch as w


def snap(count=0):
    return {"count": count, "sample": [], "hash": w.digest(count)}


class WatchTests(unittest.TestCase):
    def test_empty_baseline_is_quiet(self):
        current = {"accepted": snap(), "activity": snap(), "page": {"hash": "empty", "paper_links": []}}
        reasons, merged = w.compare({}, current)
        self.assertEqual(reasons, [])
        self.assertEqual(merged, current)

    def test_first_run_already_public_alerts(self):
        reasons, _ = w.compare({}, {"accepted": snap(5000)})
        self.assertEqual(len(reasons), 1)

    def test_first_run_page_has_papers_alerts(self):
        reasons, _ = w.compare({}, {"page": {"hash": "ready", "paper_links": ["https://openreview.net/forum?id=abc"]}})
        self.assertTrue(reasons)

    def test_unchanged_is_deduplicated(self):
        before = {"accepted": snap(5)}
        self.assertFalse(w.compare(before, copy.deepcopy(before))[0])

    def test_release_detected(self):
        self.assertTrue(w.compare({"accepted": snap()}, {"accepted": snap(5)})[0])

    def test_removal_detected(self):
        self.assertTrue(w.compare({"accepted": snap(5)}, {"accepted": snap(4)})[0])

    def test_page_change_not_called_acceptance(self):
        subject, body = w.render_message({"accepted": snap()}, ["Page changed"], [], "now")
        self.assertNotIn("Accepted Papers öffentlich", subject)
        self.assertIn("noch nicht bestätigt", body)

    def test_missing_api_never_reuses_stale_acceptance(self):
        subject, _ = w.render_message({}, ["Page changed"], ["API unavailable"], "now")
        self.assertNotIn("Accepted Papers öffentlich", subject)

    def test_partial_failure_preserves_good_state(self):
        old = {"accepted": snap(5), "page": {"hash": "old"}}
        _, merged = w.compare(old, {"page": {"hash": "new"}})
        self.assertEqual(merged["accepted"], old["accepted"])

    def test_malformed_api_does_not_mean_zero(self):
        for data in ({}, {"notes": [], "count": "0"}, {"notes": [{}], "count": 0}):
            with self.assertRaises(ValueError):
                w.notes_snapshot(data, True)

    def test_wrong_venue_is_not_accepted(self):
        with self.assertRaises(ValueError):
            w.notes_snapshot({"count": 1, "notes": [{"id": "a", "content": {"venueid": {"value": w.VENUE + "/Submission"}}}]}, True)

    def test_api_timestamps_alone_do_not_change_fingerprint(self):
        note = {"id": "a", "content": {"venueid": {"value": w.VENUE}, "title": {"value": "Test"}}, "tmdate": 10}
        first = w.notes_snapshot({"count": 1, "notes": [note]}, True)
        note["tmdate"] = 20
        self.assertEqual(first, w.notes_snapshot({"count": 1, "notes": [note]}, True))

    def test_relative_time_is_ignored(self):
        self.assertEqual(w.normalize_text("X\n5 minutes ago"), w.normalize_text("X\n1 hour ago"))

    def test_message_is_short_and_contains_target(self):
        _, body = w.render_message({"accepted": snap(123)}, ["x" * 1000], [], "now")
        self.assertIn(w.PAGE_URL, body)
        self.assertLessEqual(len(body), 951)

    def test_real_main_baseline_then_change_then_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = {"hash": "empty", "text": "NeurIPS 2026 No recent activity", "links": [], "paper_links": []}
            with patch.object(w, "STATE", root / "state.json"), patch.object(w, "REPORT", root / "report.md"), \
                 patch.object(w, "fetch_page", return_value=page), patch.object(w, "fetch_notes", return_value=snap()) as fetch, \
                 patch.dict(os.environ, {"GITHUB_OUTPUT": str(root / "output"), "GITHUB_STEP_SUMMARY": str(root / "summary")}):
                alert = root / "alert.txt"
                w.main(["--alert-file", str(alert)])
                self.assertFalse(alert.exists())
                baseline = w.STATE.read_text()
                w.main(["--alert-file", str(alert)])
                self.assertEqual(w.STATE.read_text(), baseline)
                fetch.return_value = snap(20)
                w.main(["--alert-file", str(alert)])
                self.assertIn("Accepted Papers öffentlich", alert.read_text())
                w.main(["--alert-file", str(alert)])
                self.assertFalse(alert.exists())
                self.assertEqual(json.loads(w.STATE.read_text())["sources"]["accepted"]["count"], 20)


if __name__ == "__main__":
    unittest.main()
