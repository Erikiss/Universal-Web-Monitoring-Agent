"""Offline behavioral regressions: no network, SMTP delivery, or real secrets."""
import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import pythia_google_watch as p

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
QUERY = {"q": "pythia", "tbs": "qdr:d"}


def hit(url="https://example.org/paper", title="Probing Pythia-1.4B", snippet="New research"):
    return {"url": url, "title": title, "snippet": snippet, "google_date": "1 hour ago"}


def response(items=None, next_start=None, **extra):
    data = {"search_metadata": {"status": "Success"},
            "search_parameters": {"engine": "google", **QUERY},
            "organic_results": [{"link": x["url"], "title": x["title"], "snippet": x["snippet"]}
                                for x in (items if items is not None else [hit()])],
            "search_information": {"total_results": 1}}
    if next_start is not None:
        data["serpapi_pagination"] = {"next": f"https://serpapi.com/search.json?start={next_start}"}
    data.update(extra)
    return data


def client_for(*pages, max_pages=10):
    client = p.GoogleSearch("fake-test-key", max_pages=max_pages)
    client.request = Mock(side_effect=pages)
    return client


class RelevanceTests(unittest.TestCase):
    def test_model_identity_and_sizes(self):
        for text in ("EleutherAI Pythia", "Pythia-1.4B", "pythia_70m_deduped", "Pythia 12B",
                     "Pythia and GPT-NeoX", "Pythia: A Suite for Analyzing Large Language Models",
                     "Pythia Sprachmodelle", "Pythia mechanistic interpretability"):
            with self.subTest(text=text):
                self.assertEqual(p.classify(hit(title=text))[0], "match")

    def test_physics_is_not_positive_just_because_ml_is_mentioned(self):
        for text in ("PYTHIA 8 particle physics with neural networks", "LLMs improve Pythia event generator",
                     "Pythia CERN machine learning", "Pythia prefetcher reinforcement learning",
                     "Pythia crypto AI oracle", "Pythia robotics language models", "Pythia-8 LLM interface"):
            with self.subTest(text=text):
                self.assertEqual(p.classify(hit(title=text))[0], "excluded")

    def test_explicit_model_evidence_survives_a_physics_application(self):
        self.assertEqual(p.classify(hit(title="EleutherAI Pythia-1B for particle physics"))[0], "match")

    def test_generic_ai_and_unrelated_llm_not_enough(self):
        for text in ("Pythia new AI tool", "Pythia oracle", "New LLM research", "Pythia8.3"):
            self.assertNotEqual(p.classify(hit(title=text))[0], "match")

    def test_query_terms_do_not_turn_ambiguous_result_into_match(self):
        row = hit(title="Pythia", snippet="A new product")
        row["search"] = p.FOCUSED_QUERY
        self.assertEqual(p.classify(row)[0], "unclear")

    def test_model_card_url_is_evidence(self):
        self.assertEqual(p.classify(hit(url="https://huggingface.co/EleutherAI/pythia-410m", title="Model card"))[0], "match")


class SearchTests(unittest.TestCase):
    def test_all_pages_are_consumed(self):
        client = client_for(response(next_start=10), response([hit("https://example.org/second")]))
        pages = list(client.pages(QUERY))
        self.assertEqual(len(pages), 2)
        self.assertEqual(client.request.call_args.args[1], 10)

    def test_pagination_limit_is_a_failure_after_retaining_page(self):
        iterator = client_for(response(next_start=10), max_pages=1).pages(QUERY)
        self.assertEqual(len(next(iterator)), 1)
        with self.assertRaises(p.SearchFailure):
            next(iterator)

    def test_repeated_page_is_an_error(self):
        iterator = client_for(response(next_start=10), response(next_start=20)).pages(QUERY)
        next(iterator)
        with self.assertRaises(p.SearchFailure):
            next(iterator)

    def test_backward_pagination_is_an_error(self):
        with self.assertRaises(p.SearchFailure):
            list(client_for(response(next_start=0)).pages(QUERY))

    def test_confirmed_empty_is_a_success(self):
        data = response([], search_information={"organic_results_state": "Fully empty", "total_results": 0},
                        error="Google hasn't returned any results for this query.")
        del data["organic_results"]
        self.assertEqual(list(client_for(data).pages(QUERY)), [[]])

    def test_malformed_error_or_ignored_time_filter_is_not_no_hits(self):
        samples = [response([], search_information={}), response(error="quota exhausted"),
                   response(search_metadata={"status": "Processing"}),
                   response(search_parameters={"engine": "google", "q": "pythia"}),
                   response([hit(url="javascript:alert(1)")])]
        for data in samples:
            with self.subTest(data=data), self.assertRaises(p.SearchFailure):
                list(client_for(data).pages(QUERY))

    def test_provider_error_cannot_leak_key(self):
        session = Mock()
        session.get.return_value.status_code = 403
        session.get.return_value.text = "Secret fake-test-key"
        client = p.GoogleSearch("fake-test-key", session=session)
        with self.assertRaises(p.SearchFailure) as error:
            client.request(QUERY, 0)
        self.assertNotIn("fake-test-key", str(error.exception))

    @patch.object(p.time, "sleep")
    def test_network_retries_are_bounded_and_sanitized(self, sleep):
        session = Mock()
        session.get.side_effect = requests.ConnectionError("https://host?api_key=fake-test-key")
        client = p.GoogleSearch("fake-test-key", session=session)
        with self.assertRaises(p.SearchFailure) as error:
            client.request(QUERY, 0)
        self.assertEqual(session.get.call_count, 3)
        self.assertNotIn("fake-test-key", str(error.exception))

    def test_requests_stop_before_actions_timeout(self):
        session = Mock()
        client = p.GoogleSearch("fake-test-key", session=session)
        client.deadline = 0
        with self.assertRaises(p.SearchFailure):
            client.request(QUERY, 0)
        session.get.assert_not_called()


class StateAndDeliveryTests(unittest.TestCase):
    def test_tracking_and_arxiv_formats_dedupe(self):
        self.assertEqual(p.canonical_url("https://example.org/paper?utm_source=x&key=a#s"), "https://example.org/paper?key=a")
        self.assertEqual(p.canonical_url("https://arxiv.org/pdf/2304.01373v2.pdf"), "https://arxiv.org/abs/2304.01373v2")
        self.assertNotEqual(p.canonical_url("https://example.org/?id=1"), p.canonical_url("https://example.org/?id=2"))

    def test_delays_and_outages_expand_the_lookback(self):
        state = p.new_state(NOW)
        self.assertEqual(p.search_plan(state, NOW)[0]["tbs"], "qdr:d")
        self.assertEqual(p.search_plan(state, NOW)[1]["tbs"], "qdr:d3")
        state["last_success_at"] = p.stamp(NOW - timedelta(days=10, hours=1))
        self.assertEqual(p.search_plan(state, NOW)[1]["tbs"], "qdr:d13")

    def test_future_watermark_is_not_silently_accepted(self):
        state = p.new_state(NOW + timedelta(days=1))
        with self.assertRaises(ValueError):
            p.search_plan(state, NOW)

    def test_partial_search_keeps_hits_without_advancing_watermark(self):
        old = p.new_state(NOW - timedelta(days=5))
        old["last_success_at"] = old["started_at"]
        original = copy.deepcopy(old)
        def pages(query):
            yield [hit()]
            raise p.SearchFailure("HTTP 503")
        client = Mock()
        client.pages.side_effect = pages
        state, report = p.collect(client, old, NOW)
        self.assertEqual(old, original)
        self.assertEqual(state["last_success_at"], old["last_success_at"])
        self.assertEqual(len(state["pending"]), 1)
        self.assertEqual(len(report["errors"]), 3)

    def test_repeat_queries_and_future_runs_do_not_redeliver(self):
        client = Mock()
        client.pages.return_value = [[hit()]]
        state, report = p.collect(client, p.new_state(NOW), NOW)
        self.assertEqual(report["new_count"], 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            p.save_json(path, state)
            sender = Mock()
            self.assertEqual(p.deliver_pending(path, NOW, sender), 1)
            self.assertEqual(p.deliver_pending(path, NOW, sender), 0)
            state = p.load_state(path, NOW)
            again, _ = p.collect(client, state, NOW + timedelta(days=1))
            self.assertFalse(again["pending"])
            sender.assert_called_once()

    def test_smtp_failure_keeps_unsent_queue_even_if_source_disappears(self):
        state = p.new_state(NOW)
        for name in ("one", "two"):
            item = hit("https://example.org/" + name)
            state["pending"][item["url"]] = {**item, "first_seen_at": p.stamp(NOW)}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            p.save_json(path, state)
            sender = Mock(side_effect=[None, SystemExit("SMTP failure")])
            with self.assertRaises(SystemExit):
                p.deliver_pending(path, NOW, sender)
            saved = p.load_state(path, NOW)
            self.assertEqual(len(saved["delivered"]), 1)
            self.assertEqual(len(saved["pending"]), 1)
            client = Mock()
            client.pages.return_value = [[]]
            later, _ = p.collect(client, saved, NOW + timedelta(days=7))
            self.assertEqual(later["pending"], saved["pending"])

    def test_unclear_results_are_audited_but_not_marked_delivered(self):
        client = Mock()
        client.pages.return_value = [[hit(title="Pythia", snippet="No context")]]
        state, report = p.collect(client, p.new_state(NOW), NOW)
        self.assertFalse(state["pending"])
        self.assertFalse(state["delivered"])
        self.assertIn("Unklar", p.render_report(report))

    def test_corrupt_state_is_not_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            for text in ("{", '{"schema_version":99}'):
                path.write_text(text)
                with self.assertRaises((ValueError, KeyError)):
                    p.load_state(path, NOW)
                self.assertEqual(path.read_text(), text)

    def test_missing_key_does_not_create_state(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / "state.json"
            with redirect_stderr(io.StringIO()):
                self.assertEqual(p.main(["collect", "--state", str(path)]), 1)
            self.assertFalse(path.exists())

    def test_dry_run_has_no_persistent_or_mail_side_effects(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), patch.object(p, "GoogleSearch") as factory:
            factory.return_value.pages.return_value = [[hit()]]
            path = Path(directory) / "state.json"
            report = Path(directory) / "report.md"
            archive = Path(directory) / "archive"
            with redirect_stdout(io.StringIO()):
                p.main(["collect", "--state", str(path), "--report", str(report), "--archive", str(archive), "--dry-run"])
                p.main(["deliver", "--state", str(path), "--dry-run"])
            self.assertFalse(list(Path(directory).iterdir()))


if __name__ == "__main__":
    unittest.main()
