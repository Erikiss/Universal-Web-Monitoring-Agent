"""Offline regression tests; no network, SMTP, or secrets required."""
import base64
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import music_jepa_watch as m

ABS = '''<h1>Music-JEPA: Learning a World Model of Sound from Action</h1>
<div class="submission-history">[v1] 24 Jul 2026 <b>[v2]</b> 25 Sep 2026</div>
<div class="comments"><a href="https://github.com/ZZWaang/new-name">Code</a></div>
<div>Unrelated reference [v99]</div>'''
ATOM = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2607.22000v3</id><title>Music-JEPA</title>
<summary>Code: https://github.com/ZZWaang/new-name</summary></entry></feed>'''


def meta(name=m.PROJECT_REPO, pushed="1"):
    return {"full_name": name, "name": name.split("/")[1], "pushed_at": pushed,
            "default_branch": "main", "size": 1}


def repo_client(readme="", files=None, sha="tree1"):
    client = Mock()
    tree = {"sha": sha, "tree": files or [], "truncated": False}
    def get_json(url, *args, **kwargs):
        if url.endswith("/readme"):
            return {"encoding": "base64", "content": base64.b64encode(readme.encode()).decode()} if readme else None
        return tree
    client.json.side_effect = get_json
    client.pages.return_value = []
    return client


class ParsingTests(unittest.TestCase):
    def test_arxiv_reads_only_submission_history(self):
        self.assertEqual(m.parse_arxiv_abs(ABS), {"version": 2, "links": ["https://github.com/ZZWaang/new-name"]})

    def test_arxiv_blocks_are_not_a_version(self):
        for text in ("Access denied", "<h1>Music-JEPA</h1>", '<div class="submission-history">no version</div>Music-JEPA'):
            with self.assertRaises(m.FetchError):
                m.parse_arxiv_abs(text)

    def test_atom_exact_paper_and_version(self):
        self.assertEqual(m.parse_arxiv_atom(ATOM)["version"], 3)
        with self.assertRaises(m.FetchError):
            m.parse_arxiv_atom(ATOM.replace(m.PAPER, "2607.11111"))

    def test_arxiv_api_fallback(self):
        client = Mock()
        client.get.side_effect = [m.FetchError("HTTP 503"), Mock(text=ATOM)]
        self.assertEqual(m.arxiv_snapshot(client)["version"], 3)

    def test_resource_links_and_ids(self):
        links = m.links_in('[model](https://huggingface.co/a/b) <a href="https://github.com/a/b?x=1&amp;y=2">Code</a>')
        self.assertIn("https://github.com/a/b?x=1&y=2", links)
        self.assertEqual(m.resource_id("https://github.com/a/b/tree/main", "github.com"), "a/b")
        self.assertIsNone(m.resource_id("https://huggingface.co/papers/2607.22000", "huggingface.co"))
        self.assertIsNone(m.resource_id("https://github.com.evil.test/a/b", "github.com"))


class ObservationTests(unittest.TestCase):
    def test_baseline_then_one_change_no_duplicate(self):
        watcher = m.Watcher(Mock(), m.new_state())
        watcher.observe("x", {"version": 1}, "change", m.ARXIV)
        self.assertEqual(watcher.events, [])
        watcher.observe("x", {"version": 2}, "change", m.ARXIV)
        watcher.observe("x", {"version": 2}, "change", m.ARXIV)
        self.assertEqual(len(watcher.events), 1)
        self.assertIn("1 -> 2", watcher.events[0]["detail"])

    @patch("music_jepa_watch.arxiv_snapshot")
    def test_new_version_on_initial_run_is_not_silenced(self, snapshot):
        snapshot.return_value = {"version": 2, "links": []}
        watcher = m.Watcher(Mock(), m.new_state())
        watcher.paper()
        self.assertEqual(len(watcher.events), 1)

    @patch("music_jepa_watch.arxiv_snapshot")
    def test_stale_arxiv_response_does_not_reset_baseline(self, snapshot):
        state = m.new_state()
        state["observations"]["arxiv"] = {"version": 3, "links": []}
        snapshot.return_value = {"version": 1, "links": []}
        watcher = m.Watcher(Mock(), state)
        watcher.attempt("arXiv", watcher.paper)
        self.assertEqual(state["observations"]["arxiv"]["version"], 3)
        self.assertIn("arXiv", watcher.failed)

    def test_project_invisible_script_change_alerts(self):
        client = Mock()
        client.get.return_value.text = '<h1>Music-JEPA</h1><script>let x=1</script>'
        watcher = m.Watcher(client, m.new_state())
        watcher.project()
        self.assertFalse(watcher.events)
        client.get.return_value.text = '<h1>Music-JEPA</h1><script>let x=2</script>'
        watcher.project()
        self.assertEqual(len(watcher.events), 1)

    def test_error_is_not_a_change_and_is_not_emailed_every_day(self):
        state = m.new_state()
        state["observations"]["project-page"] = {"old": "keep"}
        for expected_events in (1, 0):
            watcher = m.Watcher(Mock(), state)
            watcher.paper = Mock(side_effect=m.FetchError("503"))
            watcher.project = Mock()
            watcher.github = Mock()
            watcher.huggingface = Mock()
            watcher.run()
            self.assertEqual(len(watcher.events), expected_events)
            self.assertEqual(state["observations"]["project-page"], {"old": "keep"})
            watcher.project.assert_called_once()
        watcher = m.Watcher(Mock(), state)
        for name in ("paper", "project", "github", "huggingface"):
            setattr(watcher, name, Mock())
        watcher.run()
        self.assertNotIn("arXiv", state["errors"])

    def test_demo_alone_is_not_a_code_release(self):
        watcher = m.Watcher(repo_client(), m.new_state())
        watcher.inspect_repo(meta())
        self.assertEqual(watcher.events, [])
        self.assertIn("project-source", watcher.state["observations"])

    def test_asset_only_change_alerts(self):
        state = m.new_state()
        m.Watcher(repo_client(), state).inspect_repo(meta())
        watcher = m.Watcher(repo_client(sha="tree2"), state)
        watcher.inspect_repo(meta(pushed="2"))
        self.assertEqual(len(watcher.events), 1)
        self.assertIn("Medien/Assets", watcher.events[0]["title"])

    def test_new_code_in_demo_alerts(self):
        state = m.new_state()
        m.Watcher(repo_client(), state).inspect_repo(meta())
        client = repo_client(files=[{"type": "blob", "path": "train.py", "sha": "abc"}], sha="tree2")
        watcher = m.Watcher(client, state)
        watcher.inspect_repo(meta(pushed="2"))
        self.assertEqual(len(watcher.events), 2)
        self.assertIn("train.py", watcher.events[1]["detail"])

    def test_project_change_not_masked_by_release_endpoint_failure(self):
        state = m.new_state()
        m.Watcher(repo_client(), state).inspect_repo(meta())
        client = repo_client(sha="tree2")
        client.pages.side_effect = m.FetchError("503")
        watcher = m.Watcher(client, state)
        watcher.attempt("demo", lambda: watcher.inspect_repo(meta(pushed="2")))
        self.assertTrue(any("Medien/Assets" in e["title"] for e in watcher.events))
        self.assertIn("demo", watcher.failed)

    def test_arbitrary_author_repo_name_readme_match(self):
        watcher = m.Watcher(repo_client(readme=f"Our paper {m.PAPER}"), m.new_state())
        watcher.inspect_repo(meta("ZZWaang/bananas"))
        self.assertEqual(len(watcher.events), 1)
        self.assertIn("bananas", watcher.events[0]["title"])

    def test_unrelated_author_repo_silent_but_later_repurpose_detected(self):
        state = m.new_state()
        m.Watcher(repo_client(readme="Unrelated project"), state).inspect_repo(meta("ZZWaang/bananas"))
        self.assertFalse(state["observations"])
        watcher = m.Watcher(repo_client(readme="Music-JEPA"), state)
        watcher.inspect_repo(meta("ZZWaang/bananas", pushed="2"))
        self.assertEqual(len(watcher.events), 1)

    def test_readme_cache_does_not_re_read_unchanged_repo(self):
        client = repo_client(readme="Music-JEPA")
        state = m.new_state()
        m.Watcher(client, state).inspect_repo(meta("ZZWaang/bananas"))
        m.Watcher(client, state).inspect_repo(meta("ZZWaang/bananas"))
        self.assertEqual(sum(c.args[0].endswith("/readme") for c in client.json.call_args_list), 1)

    def test_truncated_tree_is_not_saved_as_complete(self):
        client = Mock()
        client.json.side_effect = [None, {"tree": [], "truncated": True}]
        watcher = m.Watcher(client, m.new_state())
        with self.assertRaises(m.FetchError):
            watcher.inspect_repo(meta())
        self.assertFalse(watcher.state["observations"])

    def test_self_repository_never_alerts(self):
        watcher = m.Watcher(Mock(), m.new_state())
        watcher.inspect_repo(meta(m.SELF_REPO))
        self.assertFalse(watcher.events)
        watcher.client.json.assert_not_called()

    def test_hf_gating_and_weight_paths_are_explicit(self):
        client = Mock()
        client.json.return_value = {"id": "author/music-jepa", "siblings": [{"rfilename": "model.safetensors"}],
                                    "gated": "manual", "private": False, "sha": "abc"}
        state = m.new_state()
        watcher = m.Watcher(client, state)
        watcher.inspect_model("author/music-jepa")
        self.assertEqual(len(watcher.events), 1)
        self.assertEqual(state["observations"]["hf:author/music-jepa"]["gated"], "manual")
        watcher.events.clear()
        watcher.inspect_model("author/music-jepa")
        self.assertFalse(watcher.events)


class HTTPAndCLITests(unittest.TestCase):
    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-secret"})
    def test_credentials_scoped_to_github_api(self):
        client = m.Client()
        client.session = Mock()
        client.session.get.return_value = Mock(status_code=200, content=b"{}")
        client.get(m.GH + "/repos/a/b")
        client.get(m.HF + "/api/models")
        first, second = client.session.get.call_args_list
        self.assertIn("Authorization", first.kwargs["headers"])
        self.assertNotIn("Authorization", second.kwargs["headers"])

    def test_incomplete_search_is_error(self):
        client = m.Client()
        client.get = Mock(return_value=Mock(json=lambda: {"incomplete_results": True, "items": []}))
        with self.assertRaises(m.FetchError):
            client.pages(m.GH + "/search/repositories", items_key="items")

    def test_pagination_follows_same_host_only(self):
        client = m.Client()
        client.get = Mock(return_value=Mock(json=lambda: [], links={"next": {"url": "https://evil.test"}}))
        with self.assertRaises(m.FetchError):
            client.pages(m.GH + "/users/a/repos")

    @patch("music_jepa_watch.Watcher.run", return_value=[])
    @patch("builtins.print")
    def test_dry_run_keeps_state_and_report_untouched(self, _print, _run):
        with tempfile.TemporaryDirectory() as tmp:
            state, report = Path(tmp) / "state.json", Path(tmp) / "report.md"
            state.write_text(json.dumps(m.new_state()))
            before = state.read_bytes()
            m.main(["--state-file", str(state), "--report-file", str(report), "--dry-run"])
            self.assertEqual(state.read_bytes(), before)
            self.assertFalse(report.exists())

    def test_invalid_state_fails_instead_of_resetting(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text('{"schema_version": 999}')
            with self.assertRaises(ValueError):
                m.main(["--state-file", str(state), "--dry-run"])


if __name__ == "__main__":
    unittest.main()
