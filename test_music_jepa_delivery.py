"""Offline regressions for provenance, daily/weekly routing and state migration."""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import music_jepa_watch as core
import music_jepa_delivery as d

NOW = datetime(2026, 9, 23, 10, 30, tzinfo=timezone.utc)


def watcher(state=None, now=NOW):
    return d.CategorizedWatcher(Mock(), state if state is not None else core.new_state(), now)


def candidate(w, name="other/example", files=None):
    w.observe("repo:" + name,
              {"code_files": files or ["train.py"], "weight_file_hints": {}, "releases": [], "resource_links": []},
              "GitHub-Hinweis: " + name, "https://github.com/" + name, initial=True)


def paper_change(w):
    w.observe("arxiv", {"version": 1, "links": []}, "arXiv", core.ARXIV)
    w.observe("arxiv", {"version": 2, "links": []}, "arXiv", core.ARXIV)
    w.checked.append("arXiv")


class RoutingTests(unittest.TestCase):
    def test_paper_change_daily_without_release_classification(self):
        w = watcher()
        paper_change(w)
        batches = d.route(w, NOW)
        self.assertEqual(len(batches["daily"]), 1)
        self.assertEqual(batches["daily"][0]["category"], d.DIRECT)
        self.assertIn("v1 → v2", " ".join(batches["daily"][0]["summary"]))
        self.assertFalse(batches["weekly"])

    def test_project_change_daily(self):
        w = watcher()
        for value in ("old", "new"):
            w.observe("project-page", {"html_sha256": value, "links": []}, "Projektseite", core.PROJECT)
        self.assertEqual(len(d.route(w, NOW)["daily"]), 1)

    def test_demo_asset_change_daily(self):
        w = watcher()
        for value in ("old", "new"):
            w.observe("project-source", {"tree_sha": value}, "Demo", core.PROJECT)
        self.assertEqual(len(d.route(w, NOW)["daily"]), 1)

    def test_third_party_never_triggers_daily_mail(self):
        w = watcher()
        candidate(w)
        batches = d.route(w, NOW)
        self.assertFalse(batches["daily"])
        self.assertFalse(batches["weekly"])
        self.assertEqual(len(w.delivery["pending"]), 1)

    def test_author_is_daily_but_not_a_clear_paper_change(self):
        w = watcher()
        candidate(w, "ZZWaang/new-project")
        event = d.route(w, NOW)["daily"][0]
        self.assertEqual(event["category"], d.AUTHOR)
        self.assertIn("Autorenkonto", event["origin"])

    def test_unconfirmed_hf_result_waits_for_digest(self):
        w = watcher()
        w.observe("hf:unknown/music-jepa", {"weight_file_hints": ["model.pt"], "gated": "manual"},
                  "Modell", "https://huggingface.co/unknown/music-jepa", initial=True)
        self.assertFalse(d.route(w, NOW)["daily"])
        self.assertEqual(len(w.delivery["pending"]), 1)

    def test_directly_linked_model_is_daily_author_hint(self):
        w = watcher()
        w.remember_links(["https://huggingface.co/team/model"], core.PROJECT)
        w.observe("hf:team/model", {"weight_file_hints": ["model.pt"]}, "Modell", "https://huggingface.co/team/model", initial=True)
        self.assertEqual(d.route(w, NOW)["daily"][0]["category"], d.AUTHOR)

    def test_third_party_links_never_promote_model(self):
        w = watcher()
        w.resource_links.add("https://huggingface.co/unknown/model")
        self.assertEqual(w.category("hf:unknown/model")[0], d.THIRD)

    def test_previously_seen_primary_links_keep_provenance(self):
        state = core.new_state()
        state["observations"]["project-page"] = {"links": ["https://huggingface.co/team/model"]}
        w = watcher(state)
        self.assertEqual(w.category("hf:team/model")[0], d.AUTHOR)

    def test_promotion_alarms_without_file_change_and_removes_queue(self):
        w = watcher()
        candidate(w)
        d.route(w, NOW)
        later = watcher(w.state, NOW + timedelta(days=1))
        later.remember_links(["https://github.com/other/example"], core.PROJECT)
        candidate(later)
        batches = d.route(later, NOW + timedelta(days=1))
        self.assertEqual(len(batches["daily"]), 1)
        self.assertEqual(batches["daily"][0]["category"], d.AUTHOR)
        self.assertFalse(later.delivery["pending"])

    def test_technical_error_has_separate_category(self):
        w = watcher()
        w.attempt("arXiv", Mock(side_effect=core.FetchError("503")))
        batch = d.route(w, NOW)["daily"]
        self.assertEqual(batch[0]["category"], d.TECHNICAL)
        self.assertIn("TECHNISCHE STÖRUNG", d.daily_subject(batch))


class DigestTests(unittest.TestCase):
    def prepared(self):
        w = watcher()
        candidate(w)
        d.route(w, NOW)
        return w.state

    def test_seven_day_boundary(self):
        state = self.prepared()
        early = watcher(copy.deepcopy(state), NOW + timedelta(days=7, seconds=-1))
        self.assertFalse(d.route(early, NOW + timedelta(days=7, seconds=-1))["weekly"])
        due = watcher(state, NOW + timedelta(days=7))
        self.assertEqual(len(d.route(due, NOW + timedelta(days=7))["weekly"]), 1)
        self.assertFalse(due.delivery["pending"])

    def test_quiet_week_sends_nothing(self):
        w = watcher()
        later = watcher(w.state, NOW + timedelta(days=14))
        self.assertFalse(d.route(later, NOW + timedelta(days=14))["weekly"])

    def test_missed_run_does_not_lose_digest(self):
        w = watcher(self.prepared(), NOW + timedelta(days=9))
        self.assertEqual(len(d.route(w, NOW + timedelta(days=9))["weekly"]), 1)

    def test_no_second_digest_within_seven_days(self):
        state = self.prepared()
        d.route(watcher(state, NOW + timedelta(days=7)), NOW + timedelta(days=7))
        next_day = watcher(state, NOW + timedelta(days=8))
        candidate(next_day, "another/new-project")
        self.assertFalse(d.route(next_day, NOW + timedelta(days=8))["weekly"])
        due = watcher(state, NOW + timedelta(days=14))
        self.assertEqual(len(d.route(due, NOW + timedelta(days=14))["weekly"]), 1)

    def test_last_sent_guard_survives_overdue_next_timestamp(self):
        state = self.prepared()
        state["delivery"]["last_digest_at"] = d.timestamp(NOW + timedelta(days=6))
        w = watcher(state, NOW + timedelta(days=7))
        self.assertFalse(d.route(w, NOW + timedelta(days=7))["weekly"])

    def test_serialized_queue_survives_runs(self):
        state = json.loads(json.dumps(self.prepared()))
        self.assertEqual(len(state["delivery"]["pending"]), 1)
        w = watcher(state, NOW + timedelta(days=7))
        self.assertEqual(len(d.route(w, NOW + timedelta(days=7))["weekly"]), 1)

    def test_unchanged_candidate_not_added_again(self):
        state = self.prepared()
        w = watcher(state, NOW + timedelta(days=1))
        candidate(w)
        self.assertFalse(w.events)
        d.route(w, NOW + timedelta(days=1))
        self.assertEqual(len(state["delivery"]["pending"]["repo:other/example"]["changes"]), 1)

    def test_updates_to_one_repository_bundled(self):
        state = self.prepared()
        w = watcher(state, NOW + timedelta(days=1))
        candidate(w, files=["train.py", "inference.py"])
        d.route(w, NOW + timedelta(days=1))
        self.assertEqual(len(w.delivery["pending"]), 1)
        self.assertEqual(len(w.delivery["pending"]["repo:other/example"]["changes"]), 2)

    def test_daily_change_does_not_flush_pending_third_party(self):
        w = watcher(self.prepared(), NOW + timedelta(days=1))
        paper_change(w)
        result = d.route(w, NOW + timedelta(days=1))
        self.assertEqual(len(result["daily"]), 1)
        self.assertFalse(result["weekly"])
        self.assertEqual(len(w.delivery["pending"]), 1)
        self.assertNotIn("other/example", d.render_daily(w, result["daily"], NOW))

    def test_daily_and_weekly_can_be_due_independently(self):
        w = watcher(self.prepared(), NOW + timedelta(days=7))
        paper_change(w)
        result = d.route(w, NOW + timedelta(days=7))
        self.assertEqual(len(result["daily"]), 1)
        self.assertEqual(len(result["weekly"]), 1)

    def test_v1_migration_never_reannounces_old_four_hits(self):
        state = core.new_state()
        snapshot = {"code_files": ["train.py"], "weight_file_hints": {}, "releases": [], "resource_links": []}
        for name in ("a/repo", "b/repo", "c/repo", "d/repo"):
            state["observations"]["repo:" + name] = copy.deepcopy(snapshot)
        baseline = copy.deepcopy(state["observations"])
        w = watcher(state)
        for name in ("a/repo", "b/repo", "c/repo", "d/repo"):
            candidate(w, name)
        self.assertFalse(w.events)
        self.assertFalse(d.route(w, NOW)["weekly"])
        self.assertEqual(state["observations"], baseline)

    def test_failed_delivery_can_retry_from_uncommitted_state(self):
        persisted = self.prepared()
        for _ in range(2):
            w = watcher(copy.deepcopy(persisted), NOW + timedelta(days=7))
            self.assertEqual(len(d.route(w, NOW + timedelta(days=7))["weekly"]), 1)
        self.assertEqual(len(persisted["delivery"]["pending"]), 1)


class RenderingAndCLITests(unittest.TestCase):
    def test_clear_counts_and_origins_in_report(self):
        w = watcher()
        candidate(w)
        report = d.render_report(w, d.route(w, NOW), NOW)
        self.assertIn("Tagesalarm: 0", report)
        self.assertIn("kein Tagesalarm", report)
        self.assertIn("Herkunft: GitHub-Suche", report)
        self.assertNotIn("code_files:", report)

    def test_failed_source_is_not_called_unchanged(self):
        w = watcher()
        w.failed["arXiv"] = "503"
        status = "\n".join(d.direct_status(w))
        self.assertIn("arXiv-Paper: NICHT VOLLSTÄNDIG GEPRÜFT", status)

    def test_long_code_list_is_summarized(self):
        w = watcher()
        candidate(w, files=[f"file_{n:03d}.py" for n in range(100)])
        text = " ".join(w.events[0]["summary"])
        self.assertIn("100 Code-Dateien", text)
        self.assertNotIn("file_099.py", text)
        self.assertLess(len(text), 500)

    def test_weekly_subject_body_is_unambiguously_unconfirmed(self):
        w = watcher()
        candidate(w)
        d.route(w, NOW)
        later = watcher(w.state, NOW + timedelta(days=7))
        weekly = d.route(later, NOW + timedelta(days=7))["weekly"]
        body = d.render_weekly(weekly, NOW + timedelta(days=7))
        self.assertIn("WOCHENSAMMLUNG: DRITTANBIETER", body)
        self.assertIn("KEINE bestätigten offiziellen Releases", body)

    def test_bad_delivery_state_fails_without_reset(self):
        state = core.new_state()
        state["delivery"] = {"version": 999}
        with self.assertRaises(ValueError):
            watcher(state)

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            d.parse_time("2026-09-23T10:00:00")

    @patch("builtins.print")
    @patch.object(d.CategorizedWatcher, "run", autospec=True)
    def test_daily_run_persists_pending_without_creating_email(self, run, _print):
        run.side_effect = lambda w: candidate(w)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GITHUB_OUTPUT": "", "GITHUB_STEP_SUMMARY": ""}):
            state, report, daily, weekly = [Path(tmp) / name for name in ("state.json", "report.md", "daily.txt", "weekly.txt")]
            d.main(["--state-file", str(state), "--report-file", str(report), "--alert-file", str(daily), "--weekly-alert-file", str(weekly)])
            self.assertFalse(daily.exists())
            self.assertFalse(weekly.exists())
            self.assertEqual(len(json.loads(state.read_text())["delivery"]["pending"]), 1)

    @patch("builtins.print")
    @patch.object(d.CategorizedWatcher, "run", autospec=True)
    def test_dry_run_preserves_all_files(self, run, _print):
        run.side_effect = lambda w: candidate(w)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GITHUB_OUTPUT": "", "GITHUB_STEP_SUMMARY": ""}):
            state, report = Path(tmp) / "state.json", Path(tmp) / "report.md"
            state.write_text(json.dumps(core.new_state()))
            original = state.read_bytes()
            d.main(["--state-file", str(state), "--report-file", str(report), "--dry-run"])
            self.assertEqual(state.read_bytes(), original)
            self.assertFalse(report.exists())


if __name__ == "__main__":
    unittest.main()
