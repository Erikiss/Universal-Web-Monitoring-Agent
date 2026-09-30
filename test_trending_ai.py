"""Unit tests for trending_ai.py (no network access)."""

import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock
from pathlib import Path

import trending_ai


ARXIV_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2509.01234v2</id>
    <title>Mixture of Experts routing for
      long-context agents</title>
    <published>2026-09-14T10:00:00Z</published>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2509.05678v1</id>
    <title>Long-context agents with tool use</title>
    <published>2026-09-14T11:00:00Z</published>
  </entry>
  <entry>
    <title>Entry without id is skipped</title>
  </entry>
</feed>
"""


class ParseArxivFeedTest(unittest.TestCase):
    def test_parses_entries_and_strips_version(self):
        items = trending_ai.parse_arxiv_feed(ARXIV_FEED)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], "arxiv:2509.01234")
        self.assertEqual(items[0]["url"], "https://arxiv.org/abs/2509.01234")
        # Whitespace inside the title is normalised to single spaces.
        self.assertEqual(
            items[0]["title"], "Mixture of Experts routing for long-context agents"
        )


class TokenizeTest(unittest.TestCase):
    def test_keeps_hyphenated_and_versioned_terms(self):
        self.assertEqual(
            trending_ai.tokenize("Long-Context GPT-4o vs. Llama 3.1!"),
            ["long-context", "gpt-4o", "vs.", "llama", "3.1"],
        )

    def test_drops_single_characters(self):
        self.assertNotIn("a", trending_ai.tokenize("a big model"))


class IsUsefulTest(unittest.TestCase):
    def test_rejects_terms_containing_stopwords(self):
        self.assertFalse(trending_ai.is_useful("the agent"))
        self.assertFalse(trending_ai.is_useful("neural"))

    def test_rejects_pure_numbers(self):
        self.assertFalse(trending_ai.is_useful("2026"))

    def test_accepts_topical_phrases(self):
        self.assertTrue(trending_ai.is_useful("mixture experts"))


class RankTermsTest(unittest.TestCase):
    def items(self):
        return [
            {"title": "Long-context agents everywhere", "weight": 1.0, "url": "u1"},
            {"title": "More long-context agents", "weight": 1.0, "url": "u2"},
            {"title": "Something else entirely", "weight": 1.0, "url": "u3"},
        ]

    def test_requires_a_minimum_number_of_items(self):
        ranked = trending_ai.rank_terms(self.items())
        terms = [entry["term"] for entry in ranked]
        self.assertIn("long-context agents", terms)
        self.assertNotIn("entirely", terms)

    def test_weight_moves_a_term_up(self):
        items = self.items() + [
            {"title": "Diffusion policy release", "weight": 40.0, "url": "u4"},
            {"title": "Diffusion policy benchmark", "weight": 40.0, "url": "u5"},
        ]
        ranked = trending_ai.rank_terms(items)
        self.assertEqual(ranked[0]["term"], "diffusion policy")

    def test_a_single_item_cannot_invent_a_trend(self):
        ranked = trending_ai.rank_terms(
            [{"title": "unique unrepeated phrase here", "weight": 99.0, "url": "u"}]
        )
        self.assertEqual(ranked, [])

    def test_examples_are_capped(self):
        items = [
            {"title": "retrieval augmented pipeline", "weight": 1.0, "url": f"u{i}"}
            for i in range(10)
        ]
        ranked = trending_ai.rank_terms(items)
        self.assertTrue(all(len(entry["examples"]) <= 3 for entry in ranked))


class SeenFileTest(unittest.TestCase):
    def test_roundtrip_preserves_order_and_caps_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen.json"
            trending_ai.save_seen(["a", "b", "c"], path=path, keep=2)
            self.assertEqual(json.loads(path.read_text()), ["b", "c"])
            self.assertEqual(trending_ai.load_seen(path=path), ["b", "c"])

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(trending_ai.load_seen(path=Path(tmp) / "nope.json"), [])

    def test_corrupt_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(trending_ai.load_seen(path=path), [])

    def test_dict_form_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen.json"
            path.write_text(json.dumps({"ids": ["x"]}), encoding="utf-8")
            self.assertEqual(trending_ai.load_seen(path=path), ["x"])


OPENALEX_WORK = {
    "id": "https://openalex.org/W7212325929",
    "display_name": "Maverick: Private and Verifiable LLM Inference\n  Made Practical",
    "publication_date": "2026-09-09",
    "type": "preprint",
    "doi": "https://doi.org/10.48550/arxiv.2609.10054",
    "cited_by_count": 25,
    "primary_location": {"source": {"display_name": "arXiv (Cornell University)"}},
    "authorships": [
        {"institutions": [
            {"id": "https://openalex.org/I32971472", "display_name": "Yale University"},
            {"id": "https://openalex.org/I999999", "display_name": "Unwatched University"},
        ]},
        {"institutions": [{"id": "https://openalex.org/I63966007", "display_name": "MIT"}]},
    ],
}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class OpenAlexTest(unittest.TestCase):
    def test_enabled_only_with_a_contact_or_key(self):
        with mock.patch.object(trending_ai, "OPENALEX_MAILTO", ""), \
             mock.patch.object(trending_ai, "OPENALEX_API_KEY", ""):
            self.assertFalse(trending_ai.openalex_enabled())
        with mock.patch.object(trending_ai, "OPENALEX_MAILTO", "a@b.c"):
            self.assertTrue(trending_ai.openalex_enabled())

    def test_record_matches_the_notebook_csv_layout(self):
        record = trending_ai.openalex_record(
            OPENALEX_WORK, "Yale", "Yale University", "https://openalex.org/I32971472"
        )
        self.assertEqual(list(record), trending_ai.OPENALEX_CSV_COLUMNS)
        self.assertEqual(record["primary_location"], "arXiv (Cornell University)")
        self.assertEqual(record["cited_by_count"], 25)
        # Line breaks in the title are normalised so the CSV stays one row.
        self.assertNotIn("\n", record["title"])

    def test_fetch_keeps_one_row_per_watched_institution_and_one_item_per_work(self):
        now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(trending_ai, "OUT_DIR", Path(tmp)), \
             mock.patch.object(trending_ai, "OPENALEX_SEARCHES", ["machine unlearning"]), \
             mock.patch.object(trending_ai, "OPENALEX_MAILTO", "a@b.c"), \
             mock.patch.object(trending_ai, "time") as fake_time, \
             mock.patch.object(trending_ai, "get") as fake_get:
            fake_time.sleep.return_value = None
            fake_get.return_value = FakeResponse({"results": [OPENALEX_WORK]})
            items = trending_ai.fetch_openalex(None, None, now)

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["id"], "openalex:W7212325929")
            # cited_by_count raises the attention weight above the 1.0 floor.
            self.assertAlmostEqual(items[0]["weight"], 1.5)

            params = fake_get.call_args.kwargs["params"]
            self.assertIn("from_publication_date:2026-09-08", params["filter"])
            self.assertIn("I32971472", params["filter"])
            self.assertEqual(params["mailto"], "a@b.c")
            self.assertNotIn("api_key", params)

            csv_path = Path(tmp) / "openalex_2026-09-15.csv"
            with csv_path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            # Yale and MIT are watched, the third institution is not.
            self.assertEqual([r["institution_query"] for r in rows], ["MIT", "Yale"])

    def test_api_key_is_passed_when_configured(self):
        now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(trending_ai, "OUT_DIR", Path(tmp)), \
             mock.patch.object(trending_ai, "OPENALEX_SEARCHES", ["ai"]), \
             mock.patch.object(trending_ai, "OPENALEX_API_KEY", "secret"), \
             mock.patch.object(trending_ai, "time") as fake_time, \
             mock.patch.object(trending_ai, "get") as fake_get:
            fake_time.sleep.return_value = None
            fake_get.return_value = FakeResponse({"results": []})
            trending_ai.fetch_openalex(None, None, now)
            self.assertEqual(fake_get.call_args.kwargs["params"]["api_key"], "secret")

    def test_non_json_response_is_a_source_error(self):
        class Broken:
            def json(self):
                raise ValueError("nope")

        now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        with mock.patch.object(trending_ai, "OPENALEX_MAILTO", "a@b.c"), \
             mock.patch.object(trending_ai, "get", return_value=Broken()):
            with self.assertRaises(trending_ai.SourceError):
                trending_ai.fetch_openalex(None, None, now)


class RenderReportTest(unittest.TestCase):
    def report(self, failures=()):
        now = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
        items = [
            {
                "id": "hn:1",
                "title": "Diffusion policy release",
                "url": "https://example.com/1",
                "weight": 5.0,
                "meta": "400 points",
            },
            {
                "id": "hn:2",
                "title": "Diffusion policy benchmark",
                "url": "https://example.com/2",
                "weight": 5.0,
                "meta": "",
            },
        ]
        ranked = trending_ai.rank_terms(items)
        return trending_ai.render_report(
            now, {"hackernews": items}, ranked, list(failures), {"hn:2"}
        )

    def test_contains_heading_terms_and_new_marker(self):
        text = self.report()
        self.assertIn("# Trending AI topics — 2026-09-15", text)
        self.assertIn("diffusion policy", text)
        self.assertIn("400 points", text)
        self.assertIn("🆕", text)
        # hn:1 was seen before, so it carries no marker.
        self.assertIn("[Diffusion policy release](https://example.com/1) — 400 points\n", text)

    def test_failures_are_named_in_the_report(self):
        text = self.report(failures=[("arXiv (cs.AI/LG/CL/NE)", "HTTP 503")])
        self.assertIn("Degraded run", text)
        self.assertIn("HTTP 503", text)

    def test_sources_that_failed_are_not_rendered_as_empty(self):
        text = self.report()
        self.assertNotIn("## arXiv", text)


if __name__ == "__main__":
    unittest.main()
