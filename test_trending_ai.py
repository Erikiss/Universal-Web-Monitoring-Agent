"""Unit tests for trending_ai.py (no network access)."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
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
