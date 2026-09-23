"""Offline regression tests. No live requests or email delivery."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import transition_atlas_watch as w

NOW = "2026-09-23T12:00:00Z"
CONFIG = {"known_arxiv_version": 1, "github_authors": [{"login": "example", "name": "Author"}],
          "unresolved_authors": ["Unresolved"], "hpi_url": w.HPI_URL}
REPO = {"id": 1, "full_name": "example/research", "html_url": "https://github.com/example/research",
        "pushed_at": "2026-09-20T00:00:00Z", "description": "Research", "homepage": None,
        "default_branch": "main", "archived": False, "disabled": False, "fork": False}
HPI = '<html><h1>Christian Medeiros Adriano (Chris)</h1><h1>News</h1>{}</html>'


def response(text="", data=None):
    return Mock(text=text, json=Mock(return_value=data))


class WatchTests(unittest.TestCase):
    def test_arxiv_history_maximum_not_other_paper_version(self):
        html = f'<h1>{w.PAPER_ID}</h1><div class="submission-history">[v1] old [v2] new</div>Other 9999.99999v99'
        self.assertEqual(w.parse_arxiv_html(html), {"version": 2})

    def test_arxiv_exact_identifier_fallback(self):
        self.assertEqual(w.parse_arxiv_html(f'<a href="/pdf/{w.PAPER_ID}v3">PDF</a>'), {"version": 3})

    def test_arxiv_error_page_not_default_v1(self):
        with self.assertRaises(ValueError):
            w.parse_arxiv_html('Verify your browser ' + w.PAPER_ID)

    def test_arxiv_wrong_identifier_rejected(self):
        with self.assertRaises(ValueError):
            w.parse_arxiv_html('<div class="submission-history">[v9]</div>9999.99999')

    def test_atom_strict_entry_and_version(self):
        xml = f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/{w.PAPER_ID}v4</id></entry></feed>'
        self.assertEqual(w.parse_arxiv_atom(xml), {"version": 4})
        with self.assertRaises(ValueError):
            w.parse_arxiv_atom(xml.replace(w.PAPER_ID, "9999.99999"))

    def test_repo_initial_baseline_silent(self):
        self.assertEqual(w.diff_repos(None, w.repo_snapshot([REPO]), "Author"), [])

    def test_repo_push_detected_without_keyword_filter(self):
        old = w.repo_snapshot([REPO])
        changed = dict(REPO, pushed_at="2026-09-23T00:00:00Z")
        self.assertEqual(len(w.diff_repos(old, w.repo_snapshot([changed]), "Author")), 1)

    def test_repo_stars_followers_and_updated_at_are_not_author_updates(self):
        changed = dict(REPO, stargazers_count=900, watchers_count=123, updated_at=NOW, forks_count=99)
        self.assertEqual(w.repo_snapshot([REPO]), w.repo_snapshot([changed]))

    def test_new_and_disappeared_repos(self):
        old = w.repo_snapshot([REPO])
        new = w.repo_snapshot([dict(REPO, id=2, full_name="example/new")])
        self.assertEqual(len(w.diff_repos(old, new, "Author")), 2)

    def test_release_asset_downloads_ignored_but_asset_update_detected(self):
        item = {"id": 11, "name": "Atlas", "html_url": "https://github.com/example/research/releases/tag/v1",
                "body": "Download", "assets": [{"id": 10, "name": "atlas.parquet", "size": 13,
                "updated_at": NOW, "download_count": 1}]}
        old = w.release_snapshot([item])
        changed = copy.deepcopy(item)
        changed["assets"][0]["download_count"] = 99
        self.assertEqual(old, w.release_snapshot([changed]))
        changed["assets"][0]["size"] = 14
        self.assertEqual(len(w.diff_releases(old, w.release_snapshot([changed]), "example/research")), 1)

    def test_hpi_generic_repository_link_detected(self):
        old = w.hpi_snapshot(HPI.format('<p>No news</p>'))
        new = w.hpi_snapshot(HPI.format('<p>Research: <a href="https://github.com/example/project">Code</a></p>'))
        self.assertEqual(len(w.diff_hpi(old, new)), 1)

    def test_hpi_new_atlas_text_without_link_detected(self):
        old = w.hpi_snapshot(HPI.format(''))
        new = w.hpi_snapshot(HPI.format('<p>The transition atlas is now available.</p>'))
        self.assertEqual(len(w.diff_hpi(old, new)), 1)

    def test_hpi_unrelated_link_and_layout_noise_ignored(self):
        old = w.hpi_snapshot(HPI.format('<p>Welcome</p>'))
        new = w.hpi_snapshot(HPI.format('<p class="changed">Welcome <a href="https://conference.example/2026">Workshop</a></p>'))
        self.assertEqual(w.diff_hpi(old, new), [])

    def test_hpi_tracking_parameters_and_whitespace_ignored(self):
        old = w.hpi_snapshot(HPI.format('<p>Code <a href="https://github.com/example/project">Repo</a></p>'))
        new = w.hpi_snapshot(HPI.format('<p> Code \n <a href="https://github.com/example/project/?utm_source=foo#readme"> Repo </a> </p>'))
        self.assertEqual(w.diff_hpi(old, new), [])

    def test_hpi_footer_ignored_and_error_page_rejected(self):
        old = w.hpi_snapshot(HPI.format(''))
        new = w.hpi_snapshot(HPI.format('<h1>Contact</h1><a href="https://github.com/example/footer">New footer</a>'))
        self.assertEqual(w.diff_hpi(old, new), [])
        with self.assertRaises(ValueError):
            w.hpi_snapshot('Please enable JavaScript')

    def test_event_initial_baseline_silent_then_only_new_activity(self):
        event = {"id": "11", "type": "PushEvent", "repo": {"name": "other/repo"}, "created_at": NOW}
        baseline = w.event_snapshot([event], None, NOW)
        self.assertEqual(baseline["pending"], {})
        self.assertEqual(w.event_snapshot([event], baseline, NOW)["pending"], {})
        self.assertIn("12", w.event_snapshot([dict(event, id="12")], baseline, NOW)["pending"])
        self.assertEqual(w.event_snapshot([dict(event, id="13", type="WatchEvent")], baseline, NOW)["pending"], {})

    def test_event_historical_backfill_not_alerted(self):
        event = {"id": "1", "type": "PushEvent", "repo": {"name": "other/repo"}, "created_at": "2026-09-01T00:00:00Z"}
        self.assertEqual(w.event_snapshot([event], {"seen_ids": []}, NOW)["pending"], {})

    def make_client(self, fail_repos=False):
        client = Mock()
        client.get.return_value = response(HPI.format(''))
        def pages(url):
            if '/users/' in url:
                if fail_repos:
                    raise ValueError('simulated failure')
                return [REPO]
            return []
        client.pages.side_effect = pages
        return client

    def test_collect_initial_quiet_then_arxiv_change_once(self):
        with patch.object(w, 'collect_arxiv', return_value={"version": 1}), patch.object(w, 'collect_events', return_value=[]):
            baseline, events, errors = w.collect(self.make_client(), CONFIG, {}, NOW)
        self.assertEqual(events, [])
        self.assertEqual(errors, [])
        with patch.object(w, 'collect_arxiv', return_value={"version": 2}), patch.object(w, 'collect_events', return_value=[]):
            new, events, errors = w.collect(self.make_client(), CONFIG, baseline, NOW)
            again, repeats, errors = w.collect(self.make_client(), CONFIG, new, NOW)
        self.assertEqual([e['kind'] for e in events], ['paper'])
        self.assertEqual(repeats, [])

    def test_first_run_arxiv_v2_not_silently_baselined(self):
        with patch.object(w, 'collect_arxiv', return_value={"version": 2}), patch.object(w, 'collect_events', return_value=[]):
            _, events, errors = w.collect(self.make_client(), CONFIG, {}, NOW)
        self.assertEqual([e['kind'] for e in events], ['paper'])

    def test_failed_source_preserves_previous_and_does_not_fake_removal(self):
        old = {"sources": {"repos:example": w.repo_snapshot([REPO]), "arxiv": {"version": 2}}}
        with patch.object(w, 'collect_arxiv', side_effect=ValueError), patch.object(w, 'collect_events', return_value=[]):
            new, events, errors = w.collect(self.make_client(True), CONFIG, old, NOW)
        self.assertEqual(new['sources']['repos:example'], old['sources']['repos:example'])
        self.assertEqual(new['sources']['arxiv'], {"version": 2})
        self.assertEqual(len(errors), 2)
        self.assertEqual(events, [])
        self.assertNotIn('checked_at', old)

    def test_arxiv_stale_regression_flagged_and_not_saved(self):
        with patch.object(w, 'collect_arxiv', return_value={"version": 1}), patch.object(w, 'collect_events', return_value=[]):
            new, events, errors = w.collect(self.make_client(), CONFIG, {"sources": {"arxiv": {"version": 2}}}, NOW)
        self.assertEqual(new['sources']['arxiv'], {"version": 2})
        self.assertEqual(len(errors), 1)
        self.assertEqual(events, [])

    def test_pagination_incomplete_is_error_not_empty(self):
        client = w.Client()
        client.get = Mock(return_value=response(data=[REPO] * 100))
        with self.assertRaises(ValueError):
            client.pages('https://api.github.com/test', max_pages=2)
        self.assertEqual(client.get.call_count, 2)

    def test_dry_run_does_not_write_state_or_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg, state, report, alert = [root / name for name in ('config.json', 'state.json', 'report.md', 'alert.txt')]
            cfg.write_text(json.dumps(CONFIG))
            state.write_text('{}')
            proposed = {"checked_at": NOW, "sources": {}}
            with patch.object(w, 'collect', return_value=(proposed, [], [])), patch.dict(w.os.environ, {}, clear=True):
                self.assertEqual(w.main(['--config', str(cfg), '--state', str(state), '--report', str(report),
                                         '--alert-file', str(alert), '--dry-run']), 0)
            self.assertEqual(state.read_text(), '{}')
            self.assertFalse(report.exists())
            self.assertEqual(alert.read_text(), '')

    def test_github_token_never_sent_to_hpi(self):
        client = w.Client()
        client.session.get = Mock(return_value=Mock())
        with patch.dict(w.os.environ, {"GITHUB_TOKEN": "not-a-real-token"}):
            client.get(w.HPI_URL)
            self.assertNotIn('Authorization', client.session.get.call_args.kwargs['headers'])
            client.get('https://api.github.com/users/example/repos')
            self.assertIn('Authorization', client.session.get.call_args.kwargs['headers'])


if __name__ == '__main__':
    unittest.main()
