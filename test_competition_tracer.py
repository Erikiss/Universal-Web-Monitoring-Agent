import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import competition_tracer as tracer


def config(*ids):
    return {"version": 1, "competitions": [{"id": "test", "name": "Test", "depth": "light", "sources": [
        {"id": sid, "type": "github", "repo": "test/repo", "depth": "light"} for sid in ids]}]}


def observation(events=None):
    return {"state": {"cursor": "new"}, "events": events or [], "stats": {"files": 2}}


def event(eid="1"):
    return {"id": eid, "title": "New code", "kind": "code_added", "url": "https://github.com/test/repo", "detail": "Updated."}


class StateTests(unittest.TestCase):
    def test_failed_source_preserves_state_and_other_source_progresses(self):
        cfg = config("failed", "healthy")
        original, _ = tracer.collect(cfg, {}, None, collectors={"github": lambda *_: observation()}, now="2026-11-12T07:23:00Z")
        unchanged = copy.deepcopy(original)
        def collector(source, previous, client):
            previous["cursor"] = "MUST NOT LEAK"
            if source["id"] == "failed":
                raise RuntimeError("API unavailable")
            return observation([event()])
        result, report = tracer.collect(cfg, original, None, collectors={"github": collector})
        self.assertEqual(result["sources"]["failed"], original["sources"]["failed"])
        self.assertEqual(original, unchanged)
        self.assertEqual(report["error_count"], 1)
        self.assertEqual(len(report["events"]), 1)
        self.assertEqual(len(result["outbox"]), 1)

    def test_config_edit_rebaselines_only_changed_source(self):
        cfg = config("one", "two")
        state, _ = tracer.collect(cfg, {}, None, collectors={"github": lambda *_: observation()})
        cfg["competitions"][0]["sources"][0]["repo"] = "test/other"
        seen = {}
        def collect(source, previous, client):
            seen[source["id"]] = previous
            return observation()
        _, report = tracer.collect(cfg, state, None, collectors={"github": collect})
        self.assertIsNone(seen["one"])
        self.assertIsNotNone(seen["two"])
        self.assertTrue(report["competitions"][0]["checks"][0]["warnings"])

    def test_partial_tig_work_is_visible(self):
        def collect(*_):
            return dict(observation([event()]), errors=["One submission source failed"])
        state, report = tracer.collect(config("tig"), {}, None, collectors={"github": collect})
        self.assertEqual(report["error_count"], 1)
        self.assertEqual(len(state["outbox"]), 1)
        self.assertIn("Unvollständiger Check", tracer.render_report(report))

    def test_budget_and_depth_changes_keep_pending_work(self):
        cfg = config("one")
        state, _ = tracer.collect(cfg, {}, None, collectors={"github": lambda *_: observation()})
        cfg["competitions"][0]["sources"][0].update(depth="deep", max_source_checks=99, max_branch_pages=100)
        seen = []
        def collector(source, previous, client):
            seen.append(previous)
            return observation()
        tracer.collect(cfg, state, None, collectors={"github": collector})
        self.assertEqual(seen, [{"cursor": "new"}])

    def test_duplicate_event_ids_are_scoped_per_source(self):
        state, _ = tracer.collect(config("one", "two"), {}, None, collectors={"github": lambda *_: observation([event()])})
        self.assertEqual(set(state["outbox"]), {"one:1", "two:1"})

    def test_configuration_rejects_duplicate_source_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps(config("duplicate", "duplicate")))
            with self.assertRaisesRegex(ValueError, "globally unique"):
                tracer.load_config(path)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state.json"
        self.reports = self.root / "reports"
        self.cfg = self.root / "config.json"
        self.cfg.write_text(json.dumps(config("one")))
        self.args = ["--state", str(self.state), "--config", str(self.cfg), "--report-dir", str(self.reports),
                     "--alert-file", str(self.root / "alert.txt")]
        self.env = patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.root / "outputs"), "GITHUB_STEP_SUMMARY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_at(self, date, events):
        real_collect = tracer.collect
        def fake_collect(cfg, state, client):
            return real_collect(cfg, state, client, now=date,
                                collectors={"github": lambda *_: observation(events)})
        with patch.object(tracer, "collect", side_effect=fake_collect):
            return tracer.main(self.args)

    def test_daily_digest_acknowledgement_and_late_events(self):
        self.run_at("2026-11-12T07:23:00Z", [event("a")])
        self.assertEqual(len(json.loads(self.state.read_text())["outbox"]), 1)
        # Nothing is consumed until SMTP succeeded and this mode is invoked.
        tracer.main(["--state", str(self.state), "--acknowledge-delivery"])
        self.assertEqual(json.loads(self.state.read_text())["delivered_day"], "2026-11-12")
        self.run_at("2026-11-12T08:23:00Z", [event("b")])
        state = json.loads(self.state.read_text())
        self.assertEqual(set(state["outbox"]), {"one:b"})
        self.assertNotIn("pending_digest", state)
        report = json.loads((self.reports / "2026-11-12.json").read_text())
        self.assertEqual({e["id"] for e in report["events"]}, {"a", "b"})
        self.run_at("2026-11-13T07:23:00Z", [])
        self.assertEqual(json.loads(self.state.read_text())["pending_digest"]["keys"], ["one:b"])
        self.assertIn("New code", (self.root / "alert.txt").read_text())
        latest = (self.reports / "latest.md").read_text()
        self.assertIn("New code", latest)
        self.assertIn("2026-11-12T08:23:00Z", latest)
        self.assertIn("Heute erfasst: 0", latest)

    def test_quiet_day_still_has_health_digest(self):
        self.run_at("2026-11-12T07:23:00Z", [])
        self.assertIn("pending_digest", json.loads(self.state.read_text()))
        self.assertIn("Keine neuen Ereignisse", (self.root / "alert.txt").read_text())

    def test_dry_run_does_not_write_report_state_or_mail(self):
        self.args.append("--dry-run")
        self.run_at("2026-11-12T07:23:00Z", [event()])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.reports.exists())
        self.assertFalse((self.root / "alert.txt").exists())
        self.assertIn("alert_count=0", (self.root / "outputs").read_text())

    def test_bad_state_fails_without_overwrite(self):
        self.state.write_text('{"version": 99}')
        with self.assertRaises(ValueError):
            tracer.main(self.args)
        self.assertEqual(self.state.read_text(), '{"version": 99}')


class HttpTests(unittest.TestCase):
    def test_github_credentials_not_sent_to_redirect_destination(self):
        client = tracer.Client()
        redirect = Mock(status_code=302, is_redirect=True, headers={"Location": "https://other.example/data"})
        response = Mock(status_code=200, is_redirect=False)
        client.session.get = Mock(side_effect=[redirect, response])
        with patch.dict(os.environ, {"GITHUB_TOKEN": "private-test-token"}):
            client._request("https://api.github.com/repos/test/repo")
        self.assertIn("Authorization", client.session.get.call_args_list[0].kwargs["headers"])
        self.assertNotIn("Authorization", client.session.get.call_args_list[1].kwargs["headers"])

    def test_blocked_url_schemes(self):
        for url in ("file:///etc/passwd", "http://example.com", "https://token@example.com"):
            with self.assertRaises(ValueError):
                tracer.Client()._request(url)

    def test_robots_disallow_prevents_page_request(self):
        client = tracer.Client()
        client.text = Mock(return_value="User-agent: *\nDisallow: /private/")
        with self.assertRaisesRegex(RuntimeError, "disallows"):
            client.page_text("https://example.org/private/page")
        self.assertEqual(client.text.call_count, 1)


if __name__ == "__main__":
    unittest.main()
