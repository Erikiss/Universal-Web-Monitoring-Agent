"""Offline behavioral tests for complete, repeatable competition observations."""
import base64
import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape

import competition_sources as m

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)


def entry(paper="2611.00001", version=1, published="2026-11-11T01:00:00Z", updated=None, title="A useful paper"):
    updated = updated or published
    return (f"<entry><id>http://arxiv.org/abs/{paper}v{version}</id><title>{escape(title)}</title>"
            f"<published>{published}</published><updated>{updated}</updated><summary>A result.</summary>"
            "<author><name>A. Researcher</name></author></entry>")


def feed(*entries, total=None):
    count = f"<opensearch:totalResults>{total}</opensearch:totalResults>" if total is not None else ""
    return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
            f'{count}{"".join(entries)}</feed>')


def github_client(files, content=None, commit="commit2", truncated=False):
    client = Mock(spec=["text", "json"])
    contents = content or {}
    def json_response(url, params=None):
        if "/commits/" in url:
            return {"sha": commit, "commit": {"tree": {"sha": "tree2"}}}
        if "/git/trees/" in url:
            return {"tree": [{"path": p, "type": "blob", "sha": sha, "size": len(contents.get(sha, ""))}
                             for p, sha in files.items()], "truncated": truncated}
        if "/git/blobs/" in url:
            text = contents[url.rsplit("/", 1)[-1]]
            raw = text.encode() if isinstance(text, str) else text
            return {"encoding": "base64", "size": len(raw), "content": base64.b64encode(raw).decode()}
        raise AssertionError(url)
    client.json.side_effect = json_response
    return client


class GitHubTests(unittest.TestCase):
    def test_first_run_baselines_selected_files_and_pins_tree(self):
        client = github_client({"src/a.py": "a", "README.md": "b"})
        result = m.collect_github({"repo": "test/repo", "branch": "feature/x", "paths": ["src/"]}, None, client)
        self.assertEqual(result["events"], [])
        self.assertEqual(set(result["state"]["files"]), {"src/a.py"})
        self.assertIn("feature%2Fx", client.json.call_args_list[0].args[0])
        self.assertTrue(client.json.call_args_list[1].args[0].endswith("/git/trees/tree2"))

    def test_add_change_delete_and_no_repeat(self):
        source = {"repo": "test/repo"}
        first = m.collect_github(source, None, github_client({"README.md": "1", "old.py": "2"}))["state"]
        before = copy.deepcopy(first)
        result = m.collect_github(source, first, github_client({"README.md": "3", "new.py": "4"}))
        self.assertEqual({e["kind"] for e in result["events"]}, {"code_added", "code_changed", "code_deleted"})
        self.assertEqual(first, before)
        repeat = m.collect_github(source, result["state"], github_client({"README.md": "3", "new.py": "4"}))
        self.assertEqual(repeat["events"], [])

    def test_truncated_tree_cannot_erase_existing_snapshot(self):
        old = {"files": {"a.py": {"sha": "a"}}, "commit": "old"}
        with self.assertRaisesRegex(m.CollectorError, "truncated"):
            m.collect_github({"repo": "test/repo"}, old, github_client({}, truncated=True))
        self.assertEqual(old["files"]["a.py"]["sha"], "a")

    def test_deep_diff_is_bounded_but_every_change_is_reported(self):
        source = {"repo": "test/repo", "depth": "deep", "max_diff_files": 1, "max_diff_chars": 80}
        old = {"files": {"a.py": {"sha": "old", "size": 6}}, "commit": "oldcommit"}
        client = github_client({"a.py": "new", "b.py": "b"}, {"old": "first\n", "new": "new\n" * 50, "b": "other\n"})
        result = m.collect_github(source, old, client)
        self.assertEqual(len(result["events"]), 2)
        self.assertTrue(result["events"][0]["metadata"]["diff_truncated"])
        self.assertEqual(result["events"][1]["metadata"]["diff_omitted"], "inspection_limit")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(sum("/git/blobs/" in c.args[0] for c in client.json.call_args_list), 2)

    def test_deep_diff_handles_binary_without_executing(self):
        result = m.collect_github({"repo": "test/repo", "depth": "deep"}, {"files": {}, "commit": "old"},
                                  github_client({"bin": "a"}, {"a": b"\x00\xff"}))
        self.assertEqual(result["events"][0]["metadata"]["diff_omitted"], "binary_or_size")

    def test_failed_diff_does_not_advance_snapshot(self):
        source = {"repo": "test/repo", "depth": "deep"}
        client = github_client({"a.py": "a"})
        with self.assertRaises(KeyError):
            m.collect_github(source, {"files": {}, "commit": "old"}, client)


class PageTests(unittest.TestCase):
    def test_navigation_noise_is_ignored_but_status_change_is_detected(self):
        client = Mock(spec=["text", "json"])
        source = {"url": "https://example.test/status"}
        client.text.return_value = "<title>Status</title><nav>One</nav><main>The project is running and accepting submissions.</main>"
        initial = m.collect_page(source, None, client)
        self.assertFalse(initial["events"])
        client.text.return_value = "<title>Status</title><nav>Two</nav><main>The project is running and accepting submissions.</main><script>changed()</script>"
        unchanged = m.collect_page(source, initial["state"], client)
        self.assertFalse(unchanged["events"])
        client.text.return_value = "<title>Status</title><main>The project has been discontinued. No new submissions.</main>"
        changed = m.collect_page(source, initial["state"], client)
        self.assertEqual(changed["events"][0]["kind"], "page_changed")
        self.assertFalse(changed["stats"]["javascript_evaluated"])

    def test_missing_selector_and_empty_shell_fail(self):
        client = Mock(spec=["text", "json"])
        client.text.return_value = "<script>loadScores()</script>"
        for source in ({"url": "https://example.test", "selector": "table"}, {"url": "https://example.test"}):
            with self.assertRaises(m.CollectorError):
                m.collect_page(source, None, client)

    def test_explicit_inline_json_is_monitored_canonically(self):
        source = {"url": "https://example.test", "embedded_json_selectors": ["script#scores"]}
        client = Mock(spec=["text", "json"])
        client.text.return_value = '<main>Scores for this competition.</main><script id="scores">{"a":1,"b":2}</script>'
        first = m.collect_page(source, None, client)
        client.text.return_value = '<main>Scores for this competition.</main><script id="scores">{"b":2,"a":1}</script>'
        self.assertFalse(m.collect_page(source, first["state"], client)["events"])
        client.text.return_value = '<main>Scores for this competition.</main><script id="scores">{"b":3,"a":1}</script>'
        self.assertEqual(len(m.collect_page(source, first["state"], client)["events"]), 1)

    def test_invalid_embedded_json_fails(self):
        client = Mock(spec=["text", "json"])
        client.text.return_value = '<main>Scores for this competition.</main><script id="scores">not json</script>'
        with self.assertRaises(m.CollectorError):
            m.collect_page({"url": "https://example.test", "embedded_json_selectors": ["#scores"]}, None, client)

    def test_changed_paper_target_is_detected_with_same_visible_label(self):
        client = Mock(spec=["text", "json"])
        source = {"url": "https://example.test/research/", "selector": "main"}
        client.text.return_value = '<nav><a href="/v1">Navigation</a></nav><main>Latest research publication: <a href="papers/v1">Paper</a></main>'
        first = m.collect_page(source, None, client)
        self.assertIn("https://example.test/research/papers/v1", first["state"]["text"])
        client.text.return_value = '<nav><a href="/v2">Navigation</a></nav><main>Latest research publication: <a href="papers/v1">Paper</a></main>'
        self.assertFalse(m.collect_page(source, first["state"], client)["events"])
        client.text.return_value = '<main>Latest research publication: <a href="https://arxiv.org/abs/2611.00001">Paper</a></main>'
        result = m.collect_page(source, first["state"], client)
        self.assertEqual(len(result["events"]), 1)
        self.assertIn("https://arxiv.org/abs/2611.00001", result["events"][0]["metadata"]["diff"])


@patch("competition_sources.utc_now", return_value=NOW)
class ArxivTests(unittest.TestCase):
    def test_initial_run_recent_submissions_only(self, _now):
        client = Mock(spec=["text", "json"])
        client.text.return_value = feed(entry(), entry("2505.16135", version=2, published="2025-05-22T02:24:35Z", updated="2026-11-10T01:00:00Z"), total=2)
        result = m.collect_arxiv({"query": 'all:"Core War"'}, None, client)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["kind"], "paper_published")
        self.assertEqual(len(result["state"]["seen"]), 2)
        self.assertEqual(client.text.call_args.kwargs["params"]["sortBy"], "lastUpdatedDate")

    def test_versions_are_updates_and_repeat_is_silent(self, _now):
        client = Mock(spec=["text", "json"])
        client.text.return_value = feed(entry(version=2, updated="2026-11-12T01:00:00Z"), total=1)
        old = {"last_success": "2026-11-11T09:00:00Z", "seen": {"2611.00001": {"version": 1, "updated": "2026-11-11T01:00:00Z"}}}
        result = m.collect_arxiv({"query": "x"}, old, client)
        self.assertEqual(result["events"][0]["kind"], "paper_updated")
        self.assertEqual(result["events"][0]["published"], "2026-11-11T01:00:00Z")
        self.assertEqual(result["events"][0]["updated"], "2026-11-12T01:00:00Z")
        self.assertEqual(result["events"][0]["metadata"]["version"], 2)
        self.assertFalse(m.collect_arxiv({"query": "x"}, result["state"], client)["events"])

    def test_outage_catchup_pages_until_old_success_boundary(self, _now):
        client = Mock(spec=["text", "json"])
        client.text.side_effect = [
            feed(entry("2611.00001", updated="2026-11-11T01:00:00Z"), entry("2610.00002", published="2026-10-22T01:00:00Z"), total=4),
            feed(entry("2610.00003", published="2026-10-20T01:00:00Z"), entry("2610.00004", published="2026-10-10T01:00:00Z"), total=4)]
        old = {"last_success": "2026-10-21T09:00:00Z", "seen": {}}
        result = m.collect_arxiv({"query": "x", "page_size": 2}, old, client)
        self.assertEqual(len(result["events"]), 3)
        self.assertEqual(result["stats"]["pages"], 2)
        self.assertEqual(client.text.call_args_list[1].kwargs["params"]["start"], 2)

    def test_page_bound_overflow_is_failure(self, _now):
        client = Mock(spec=["text", "json"])
        client.text.return_value = feed(entry(), total=50)
        with self.assertRaisesRegex(m.CollectorError, "max_pages"):
            m.collect_arxiv({"query": "x", "page_size": 1, "max_pages": 1}, None, client)

    def test_error_feed_and_html_and_bad_timestamp_fail(self, _now):
        client = Mock(spec=["text", "json"])
        bad = [feed('<entry><id>http://arxiv.org/api/errors#incorrect_id_format</id><title>Error</title></entry>'),
               "<html>Unavailable</html>", feed(entry(published="bad-date"))]
        for response in bad:
            client.text.return_value = response
            with self.assertRaises(m.CollectorError):
                m.collect_arxiv({"query": "x"}, None, client)

    def test_seed_update_is_found_outside_discovery_results(self, _now):
        source = {"query": "x", "seed_ids": ["2505.16135"]}
        client = Mock(spec=["text", "json"])
        client.text.side_effect = [feed(total=0), feed(entry("2505.16135", version=2, published="2025-05-22T01:00:00Z", updated="2026-11-10T01:00:00Z"), total=1)]
        old = {"last_success": "2026-11-11T09:00:00Z", "seen": {"2505.16135": {"version": 1, "updated": "2025-05-22T01:00:00Z"}}}
        result = m.collect_arxiv(source, old, client)
        self.assertEqual(result["events"][0]["kind"], "paper_updated")
        self.assertEqual(client.text.call_args.kwargs["params"]["id_list"], "2505.16135")

    def test_missing_seed_fails(self, _now):
        client = Mock(spec=["text", "json"])
        client.text.return_value = feed(total=0)
        with self.assertRaisesRegex(m.CollectorError, "every requested seed"):
            m.collect_arxiv({"seed_ids": ["2505.16135"]}, None, client)

    def test_stale_version_cannot_regress_state(self, _now):
        client = Mock(spec=["text", "json"])
        client.text.return_value = feed(entry(), total=1)
        old = {"last_success": "2026-11-11T09:00:00Z", "seen": {"2611.00001": {"version": 2, "updated": "2026-11-11T01:00:00Z"}}}
        with self.assertRaisesRegex(m.CollectorError, "older version"):
            m.collect_arxiv({"query": "x"}, old, client)
        self.assertEqual(old["seen"]["2611.00001"]["version"], 2)

    def test_empty_result_is_success_not_an_error(self, _now):
        client = Mock(spec=["text", "json"])
        client.text.return_value = feed(total=0)
        result = m.collect_arxiv({"query": "x"}, None, client)
        self.assertFalse(result["events"])
        self.assertEqual(result["state"]["last_success"], "2026-11-12T09:00:00Z")

    def test_unsorted_response_is_failure(self, _now):
        client = Mock(spec=["text", "json"])
        client.text.return_value = feed(entry(updated="2026-11-10T01:00:00Z"), entry("2611.00002", updated="2026-11-11T01:00:00Z"), total=2)
        with self.assertRaisesRegex(m.CollectorError, "sorted"):
            m.collect_arxiv({"query": "x"}, None, client)

    def test_empty_feed_missing_schema_metadata_is_failure(self, _now):
        client = Mock(spec=["text", "json"])
        client.text.return_value = feed()
        with self.assertRaisesRegex(m.CollectorError, "missing totalResults"):
            m.collect_arxiv({"query": "x"}, None, client)

    def test_unseen_old_paper_is_discovered_without_claiming_observed_revision(self, _now):
        client = Mock(spec=["text", "json"])
        client.text.return_value = feed(entry("2505.16135", version=2, published="2025-05-22T01:00:00Z", updated="2026-11-12T01:00:00Z"), total=1)
        result = m.collect_arxiv({"query": "x"}, {"last_success": "2026-11-11T09:00:00Z", "seen": {}}, client)
        self.assertEqual(result["events"][0]["kind"], "paper_discovered")
        self.assertIsNone(result["events"][0]["metadata"]["previous_version"])

    def test_seed_response_can_omit_total_results(self, _now):
        client = Mock(spec=["text", "json"])
        client.text.return_value = feed(entry())
        result = m.collect_arxiv({"seed_ids": ["2611.00001"]}, None, client)
        self.assertEqual(len(result["events"]), 1)


if __name__ == "__main__":
    unittest.main()
