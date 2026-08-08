"""Offline unit tests for lw_filter.py — all HTTP is mocked, so the suite runs
without network access: python -m unittest test_lw_filter.py
"""

import json
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import requests
from requests.structures import CaseInsensitiveDict

import lw_filter as lw

LONG_HTML = (
    "<p>" + "alignment interpretability transformer gradient " * 500 + "</p>"
    "<figure><img src='chart.png' alt='Figure 1: a chart of the loss'/></figure>"
    "<table><tr><td>scaling law</td></tr></table>"
)


class FakeResponse:
    def __init__(self, status_code=200, *, headers=None, text="", elapsed=0.05, json_data=None):
        self.status_code = status_code
        self.headers = CaseInsensitiveDict(headers or {})
        self._json = json_data
        self.text = json.dumps(json_data) if json_data is not None else text
        self.elapsed = timedelta(seconds=elapsed)

    def json(self):
        if self._json is None:
            raise ValueError("No JSON object could be decoded")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    """Replays a queued list of responses (or exceptions) in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def graphql_payload(posts, total=None):
    return {"data": {"posts": {"totalCount": total, "results": posts}}}


def gql_post(post_id, *, title="A post", html=LONG_HTML, frontpage=True, word_count=2500):
    return {
        "_id": post_id,
        "title": title,
        "slug": "a-post",
        "postedAt": "2026-08-07T10:00:00.000Z",
        "frontpageDate": "2026-08-07T11:00:00.000Z" if frontpage else None,
        "baseScore": 42,
        "wordCount": word_count,
        "contents": {"html": html},
        "user": {"displayName": "Alice", "username": "alice"},
    }


FEED_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>LessWrong</title>
    {items}
  </channel>
</rss>
"""

FEED_ITEM = """<item>
  <title><![CDATA[{title}]]></title>
  <description><![CDATA[{html}<br/><br/><a href="https://www.lesswrong.com/posts/{pid}/slug#comments">Discuss</a>]]></description>
  <dc:creator><![CDATA[Alice]]></dc:creator>
  <pubDate>Fri, 07 Aug 2026 10:00:00 GMT</pubDate>
  <guid isPermaLink="false">{pid}</guid>
  <link>https://www.lesswrong.com/posts/{pid}/slug</link>
</item>"""


def feed_xml(*post_ids, html=LONG_HTML, title="A post"):
    items = "".join(
        FEED_ITEM.format(pid=pid, html=html, title=title) for pid in post_ids
    )
    return FEED_TEMPLATE.format(items=items)


class NoSleepTestCase(unittest.TestCase):
    """Removes every real delay so the retry paths run instantly."""

    def setUp(self):
        patches = [
            mock.patch.object(lw.time, "sleep"),
            mock.patch.object(lw, "CRAWL_DELAY", 0.0),
            mock.patch.object(lw, "_last_request", 0.0),
        ]
        self.sleep = patches[0].start()
        for patch in patches[1:]:
            patch.start()
        for patch in patches:
            self.addCleanup(patch.stop)


class RetryAfterTests(unittest.TestCase):
    def test_delta_seconds(self):
        self.assertEqual(lw.parse_retry_after("120"), 120.0)
        self.assertEqual(lw.parse_retry_after("1.5"), 1.5)

    def test_http_date(self):
        soon = datetime.now(timezone.utc) + timedelta(seconds=90)
        stamp = soon.strftime("%a, %d %b %Y %H:%M:%S GMT")
        parsed = lw.parse_retry_after(stamp)
        self.assertIsNotNone(parsed)
        self.assertTrue(60 <= parsed <= 120, parsed)

    def test_past_http_date_is_clamped_to_zero(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        stamp = past.strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.assertEqual(lw.parse_retry_after(stamp), 0.0)

    def test_garbage_and_missing(self):
        self.assertIsNone(lw.parse_retry_after("soon"))
        self.assertIsNone(lw.parse_retry_after(None))
        self.assertIsNone(lw.parse_retry_after(""))


class StandingBlockDetectionTests(unittest.TestCase):
    def test_bare_fast_429_is_a_standing_block(self):
        response = FakeResponse(429, headers={"content-type": "text/html"}, elapsed=0.02)
        self.assertTrue(lw.looks_like_standing_block(response))

    def test_budget_headers_mean_a_real_limiter(self):
        response = FakeResponse(
            429, headers={"content-type": "text/html", "X-RateLimit-Remaining": "0"}, elapsed=0.02
        )
        self.assertFalse(lw.looks_like_standing_block(response))

    def test_retry_after_means_a_real_limiter(self):
        response = FakeResponse(429, headers={"Retry-After": "30"}, elapsed=0.02)
        self.assertFalse(lw.looks_like_standing_block(response))

    def test_json_body_means_the_app_answered(self):
        response = FakeResponse(429, headers={"content-type": "application/json"}, elapsed=0.02)
        self.assertFalse(lw.looks_like_standing_block(response))

    def test_slow_response_means_the_app_answered(self):
        response = FakeResponse(429, headers={"content-type": "text/html"}, elapsed=1.2)
        self.assertFalse(lw.looks_like_standing_block(response))


class RequestTests(NoSleepTestCase):
    def test_retries_a_real_429_then_succeeds(self):
        session = FakeSession(
            [
                FakeResponse(429, headers={"Retry-After": "1"}),
                FakeResponse(200, json_data={"ok": True}),
            ]
        )
        response = lw.request(session, "GET", "https://example.test", label="t")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 2)

    def test_standing_block_gives_up_after_the_strike_limit(self):
        session = FakeSession([FakeResponse(429, headers={"content-type": "text/html"})] * 10)
        with mock.patch.object(lw, "BLOCK_STRIKES", 2):
            with self.assertRaises(lw.BlockedError) as ctx:
                lw.request(session, "POST", "https://example.test", label="t")
        self.assertIn("edge deny rule", str(ctx.exception))
        # Two strikes, not the full MAX_429_RETRIES budget.
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(self.sleep.call_count, 1)

    def test_403_is_never_retried(self):
        session = FakeSession([FakeResponse(403, text="denied")])
        with self.assertRaises(lw.BlockedError):
            lw.request(session, "GET", "https://example.test", label="t")
        self.assertEqual(len(session.calls), 1)

    def test_exhausted_rate_limit_budget_raises(self):
        session = FakeSession([FakeResponse(429, headers={"Retry-After": "1"})] * 10)
        with mock.patch.object(lw, "MAX_429_RETRIES", 3):
            with self.assertRaises(lw.RateLimitExceededError):
                lw.request(session, "GET", "https://example.test", label="t")
        self.assertEqual(len(session.calls), 3)
        # No sleep after the final attempt.
        self.assertEqual(self.sleep.call_count, 2)

    def test_retry_after_is_capped(self):
        session = FakeSession(
            [FakeResponse(429, headers={"Retry-After": "99999"}), FakeResponse(200, json_data={})]
        )
        with mock.patch.object(lw, "RETRY_AFTER_CAP", 300):
            lw.request(session, "GET", "https://example.test", label="t")
        (delay,), _ = self.sleep.call_args
        self.assertLessEqual(delay, 300 * 1.2)

    def test_network_errors_retry_then_raise(self):
        session = FakeSession([requests.ConnectionError("boom")] * 5)
        with mock.patch.object(lw, "MAX_RETRIES", 3):
            with self.assertRaises(lw.FetchError) as ctx:
                lw.request(session, "GET", "https://example.test", label="t")
        # A run of network errors is not a rate limit and must not be labelled one.
        self.assertNotIsInstance(ctx.exception, lw.RateLimitExceededError)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_one_flaky_view_does_not_abort_the_other_feed_views(self):
        session = FakeSession(
            [requests.ConnectionError("boom"), FakeResponse(text=feed_xml("abc123"))]
        )
        notes = []
        with mock.patch.object(lw, "POST_SCOPE", "all"), mock.patch.object(lw, "MAX_RETRIES", 1):
            posts = lw.fetch_via_feed(session, "2026-07-25T00:00:00+00:00", notes)
        self.assertEqual([post["id"] for post in posts], ["abc123"])

    def test_a_stray_429_does_not_relabel_an_error_exhaustion(self):
        # Classifying on "did we ever see a 429" would make fetch_via_feed
        # abandon its remaining views over an unrelated network blip.
        session = FakeSession(
            [FakeResponse(429, headers={"Retry-After": "1"})] + [requests.ConnectionError("boom")] * 3
        )
        with mock.patch.object(lw, "MAX_RETRIES", 3):
            with self.assertRaises(lw.FetchError) as ctx:
                lw.request(session, "GET", "https://example.test", label="t")
        self.assertNotIsInstance(ctx.exception, lw.RateLimitExceededError)

    def test_an_intervening_5xx_resets_the_strike_counter(self):
        # A 5xx proves the request reached an origin, which contradicts the
        # edge-deny-rule conclusion the strike counter encodes.
        session = FakeSession(
            [
                FakeResponse(429, headers={"content-type": "text/html"}),
                FakeResponse(503),
                FakeResponse(429, headers={"content-type": "text/html"}),
                FakeResponse(200, json_data={"ok": True}),
            ]
        )
        with mock.patch.object(lw, "BLOCK_STRIKES", 2):
            response = lw.request(session, "GET", "https://example.test", label="t")
        self.assertEqual(response.status_code, 200)

    def test_the_retry_budget_is_bounded_by_wall_clock(self):
        session = FakeSession([FakeResponse(429, headers={"Retry-After": "300"})] * 10)
        lw.start_deadline()
        with mock.patch.object(lw, "TIME_BUDGET_SECONDS", 30), mock.patch.object(lw, "_deadline", lw.time.monotonic() + 30):
            with self.assertRaises(lw.TimeBudgetExceededError):
                lw.request(session, "GET", "https://example.test", label="t")

    def test_server_errors_retry(self):
        session = FakeSession([FakeResponse(503), FakeResponse(200, json_data={})])
        response = lw.request(session, "GET", "https://example.test", label="t")
        self.assertEqual(response.status_code, 200)

    def test_persistent_4xx_is_not_retried(self):
        session = FakeSession([FakeResponse(404, text="nope")])
        with self.assertRaises(lw.FetchError):
            lw.request(session, "GET", "https://example.test", label="t")
        self.assertEqual(len(session.calls), 1)


class GraphqlTransportTests(NoSleepTestCase):
    def test_uses_the_selector_form_and_paginates(self):
        page1 = graphql_payload([gql_post(f"id{i}") for i in range(2)], total=3)
        page2 = graphql_payload([gql_post("id2")], total=3)
        session = FakeSession([FakeResponse(json_data=page1), FakeResponse(json_data=page2)])
        notes = []
        with mock.patch.object(lw, "PAGE_SIZE", 2), mock.patch.object(lw, "POST_LIMIT", 10):
            posts = lw.fetch_via_graphql(session, "2026-07-25T00:00:00+00:00", notes)
        self.assertEqual([post["id"] for post in posts], ["id0", "id1", "id2"])
        body = session.calls[0][2]["json"]
        self.assertIn("selector: { new: { after: $after } }", body["query"])
        self.assertNotIn("terms", body["query"])
        self.assertEqual(session.calls[1][2]["json"]["variables"]["offset"], 2)

    def test_a_server_side_page_clamp_is_not_end_of_window(self):
        # Server honours limit=50 with only 2 rows per page; totalCount says 4.
        pages = [
            FakeResponse(json_data=graphql_payload([gql_post("id0"), gql_post("id1")], total=4)),
            FakeResponse(json_data=graphql_payload([gql_post("id2"), gql_post("id3")], total=4)),
        ]
        session = FakeSession(pages)
        notes = []
        with mock.patch.object(lw, "PAGE_SIZE", 50), mock.patch.object(lw, "POST_LIMIT", 100):
            posts = lw.fetch_via_graphql(session, "2026-07-25T00:00:00+00:00", notes)
        self.assertEqual([p["id"] for p in posts], ["id0", "id1", "id2", "id3"])
        self.assertEqual(session.calls[1][2]["json"]["variables"]["offset"], 2)
        self.assertEqual(notes, [])

    def test_a_short_fetch_does_not_blame_post_limit(self):
        # totalCount claims 9, but the server runs dry after 1 — POST_LIMIT
        # (100) was never the binding constraint, so it must not be blamed.
        session = FakeSession(
            [
                FakeResponse(json_data=graphql_payload([gql_post("id0")], total=9)),
                FakeResponse(json_data=graphql_payload([], total=9)),
            ]
        )
        notes = []
        with mock.patch.object(lw, "PAGE_SIZE", 50), mock.patch.object(lw, "POST_LIMIT", 100):
            lw.fetch_via_graphql(session, "2026-07-25T00:00:00+00:00", notes)
        self.assertTrue(notes and "server stopped returning results" in notes[0], notes)
        self.assertNotIn("POST_LIMIT=100 truncated", " ".join(notes))

    def test_post_limit_truncation_is_still_named_correctly(self):
        posts = [gql_post(f"id{i}") for i in range(4)]
        session = FakeSession([FakeResponse(json_data=graphql_payload(posts, total=57))])
        notes = []
        with mock.patch.object(lw, "PAGE_SIZE", 4), mock.patch.object(lw, "POST_LIMIT", 4):
            lw.fetch_via_graphql(session, "2026-07-25T00:00:00+00:00", notes)
        self.assertTrue(notes and "POST_LIMIT=4 truncated" in notes[0], notes)

    def test_truncation_is_reported(self):
        payload = graphql_payload([gql_post(f"id{i}") for i in range(2)], total=57)
        session = FakeSession([FakeResponse(json_data=payload)])
        notes = []
        with mock.patch.object(lw, "PAGE_SIZE", 2), mock.patch.object(lw, "POST_LIMIT", 2):
            lw.fetch_via_graphql(session, "2026-07-25T00:00:00+00:00", notes)
        self.assertTrue(any("truncated the window" in note for note in notes), notes)

    def test_a_server_that_ignores_offset_does_not_loop_forever(self):
        page = graphql_payload([gql_post("id0"), gql_post("id1")], total=99)
        session = FakeSession([FakeResponse(json_data=page) for _ in range(20)])
        with mock.patch.object(lw, "PAGE_SIZE", 2), mock.patch.object(lw, "POST_LIMIT", 50):
            posts = lw.fetch_via_graphql(session, "2026-07-25T00:00:00+00:00", [])
        self.assertEqual([post["id"] for post in posts], ["id0", "id1"])
        self.assertEqual(len(session.calls), 2)

    def test_non_json_body_is_a_fetch_error_with_a_snippet(self):
        session = FakeSession([FakeResponse(200, text="<html>Attention Required</html>")])
        with self.assertRaises(lw.FetchError) as ctx:
            lw.fetch_via_graphql(session, "2026-07-25T00:00:00+00:00", [])
        self.assertIn("Attention Required", str(ctx.exception))

    def test_rate_limit_error_code_is_classified_without_substring_matching(self):
        payload = {"errors": [{"message": "slow down", "extensions": {"code": "TOO_MANY_REQUESTS"}}]}
        session = FakeSession([FakeResponse(json_data=payload)])
        with self.assertRaises(lw.RateLimitExceededError):
            lw.fetch_via_graphql(session, "2026-07-25T00:00:00+00:00", [])

    def test_partial_data_is_kept_and_noted(self):
        payload = graphql_payload([gql_post("id0")], total=1)
        payload["errors"] = [{"message": "field x failed", "extensions": {"code": "INTERNAL_SERVER_ERROR"}}]
        session = FakeSession([FakeResponse(json_data=payload)])
        notes = []
        with mock.patch.object(lw, "PAGE_SIZE", 5), mock.patch.object(lw, "POST_LIMIT", 5):
            posts = lw.fetch_via_graphql(session, "2026-07-25T00:00:00+00:00", notes)
        self.assertEqual(len(posts), 1)
        self.assertTrue(any("partial data" in note for note in notes), notes)


class FeedTransportTests(NoSleepTestCase):
    def test_parses_guid_html_and_strips_the_discuss_link(self):
        posts = lw.parse_feed(feed_xml("abc123"), "rss")
        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual(post["id"], "abc123")
        self.assertNotIn("Discuss", post["html"])
        self.assertIn("<figure>", post["html"])
        self.assertEqual(post["author"], "Alice")
        self.assertTrue(post["postedAt"].startswith("2026-08-07T10:00:00"))
        self.assertIsNone(post["baseScore"])

    def test_naive_rfc822_date_is_treated_as_utc(self):
        xml = feed_xml("abc123").replace(
            "Fri, 07 Aug 2026 10:00:00 GMT", "Fri, 07 Aug 2026 10:00:00 -0000"
        )
        post = lw.parse_feed(xml, "rss")[0]
        self.assertEqual(post["postedAt"], "2026-08-07T10:00:00+00:00")

    def test_frontpage_membership_upgrades_an_unconfirmed_section(self):
        session = FakeSession(
            [
                FakeResponse(text=feed_xml("abc123", "def456")),
                FakeResponse(text=feed_xml("abc123")),
            ]
        )
        notes = []
        with mock.patch.object(lw, "POST_SCOPE", "all"), mock.patch.object(lw, "POST_LIMIT", 10):
            posts = lw.fetch_via_feed(session, "2026-07-25T00:00:00+00:00", notes)
        by_id = {post["id"]: post for post in posts}
        self.assertEqual(len(by_id), 2)
        self.assertEqual(by_id["abc123"]["section"], "Frontpage")
        self.assertTrue(by_id["abc123"]["sectionConfirmed"])
        self.assertFalse(by_id["def456"]["sectionConfirmed"])
        self.assertTrue(any("caps at 10 items" in note for note in notes), notes)

    def test_request_url_stays_cacheable(self):
        # A daily-changing query param would be part of the edge cache key and
        # guarantee a MISS, defeating the reason this transport exists.
        session = FakeSession([FakeResponse(text=feed_xml("abc123"))] * 2)
        with mock.patch.object(lw, "POST_SCOPE", "all"):
            lw.fetch_via_feed(session, "2026-07-25T12:34:56+00:00", [])
        for _, _, kwargs in session.calls:
            self.assertNotIn("after", kwargs["params"])
        self.assertEqual(session.calls[0][2]["params"], {"view": "rss"})

    def test_malformed_xml_from_one_view_does_not_kill_the_run(self):
        session = FakeSession(
            [FakeResponse(text="<rss><channel>"), FakeResponse(text=feed_xml("abc123"))]
        )
        notes = []
        with mock.patch.object(lw, "POST_SCOPE", "all"):
            posts = lw.fetch_via_feed(session, "2026-07-25T00:00:00+00:00", notes)
        self.assertEqual([post["id"] for post in posts], ["abc123"])
        self.assertTrue(any("malformed XML" in note for note in notes), notes)

    def test_a_block_on_the_first_view_aborts_the_transport(self):
        session = FakeSession([FakeResponse(429, headers={"content-type": "text/html"})] * 4)
        with mock.patch.object(lw, "POST_SCOPE", "all"), mock.patch.object(lw, "BLOCK_STRIKES", 2):
            with self.assertRaises(lw.BlockedError):
                lw.fetch_via_feed(session, "2026-07-25T00:00:00+00:00", [])
        self.assertEqual(len(session.calls), 2)


class TransportChainTests(NoSleepTestCase):
    def test_falls_back_from_graphql_to_the_feed(self):
        session = FakeSession(
            [
                FakeResponse(429, headers={"content-type": "text/html"}),
                FakeResponse(429, headers={"content-type": "text/html"}),
                FakeResponse(text=feed_xml("abc123")),
                FakeResponse(text=feed_xml("abc123")),
            ]
        )
        notes = []
        with mock.patch.object(lw, "TRANSPORT", "auto"), \
             mock.patch.object(lw, "BLOCK_STRIKES", 2), \
             mock.patch.object(lw, "POST_SCOPE", "all"):
            posts, transport = lw.fetch_posts(session, "2026-07-25T00:00:00+00:00", notes)
        self.assertEqual(transport, "feed")
        self.assertEqual([post["id"] for post in posts], ["abc123"])

    def test_all_transports_blocked_raises(self):
        session = FakeSession([FakeResponse(403, text="denied")] * 4)
        with mock.patch.object(lw, "TRANSPORT", "auto"), mock.patch.object(lw, "POST_SCOPE", "all"):
            with self.assertRaises(lw.FetchError) as ctx:
                lw.fetch_posts(session, "2026-07-25T00:00:00+00:00", [])
        self.assertIn("all transports failed", str(ctx.exception))

    def test_pinning_the_transport_skips_the_other(self):
        session = FakeSession([FakeResponse(json_data=graphql_payload([gql_post("id0")], total=1))])
        with mock.patch.object(lw, "TRANSPORT", "graphql"):
            posts, transport = lw.fetch_posts(session, "2026-07-25T00:00:00+00:00", [])
        self.assertEqual(transport, "graphql")
        self.assertEqual(len(posts), 1)


class ClassificationTests(unittest.TestCase):
    def test_a_long_visual_ml_post_matches_on_both_transports(self):
        from_graphql = lw.normalize_graphql_post(gql_post("id0"))
        from_feed = lw.parse_feed(feed_xml("id0"), "rss")[0]
        for post in (from_graphql, from_feed):
            with self.subTest(section=post["section"]):
                result = lw.classify(post)
                self.assertIsNotNone(result)
                self.assertGreaterEqual(result["visualizationScore"], 2)
                self.assertGreaterEqual(result["topicScore"], 1)

    def test_word_count_is_estimated_when_the_feed_omits_it(self):
        post = lw.parse_feed(feed_xml("id0"), "rss")[0]
        self.assertIsNone(post["wordCount"])
        self.assertGreater(lw.estimate_words(post), lw.MIN_WORDS)

    def test_short_post_is_rejected(self):
        post = lw.normalize_graphql_post(gql_post("id0", html="<p>short</p>", word_count=10))
        self.assertIsNone(lw.classify(post))

    def test_unconfirmed_section_is_labelled(self):
        post = lw.parse_feed(feed_xml("id0"), "rss")[0]
        self.assertIn("unconfirmed", lw.classify(post)["section"])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "2026-08-08.md"

    def test_empty_report_never_overwrites_a_report_with_matches(self):
        self.path.write_text("# old\n\nMatches: 3\n", encoding="utf-8")
        self.assertFalse(lw.write_report(self.path, "# new\n\nMatches: 0\n", 0))
        self.assertIn("Matches: 3", self.path.read_text(encoding="utf-8"))

    def test_a_report_with_matches_may_overwrite(self):
        self.path.write_text("# old\n\nMatches: 3\n", encoding="utf-8")
        self.assertTrue(lw.write_report(self.path, "# new\n\nMatches: 5\n", 5))
        self.assertIn("Matches: 5", self.path.read_text(encoding="utf-8"))

    def test_a_rerun_may_not_shrink_the_days_match_count(self):
        # The second run of a day only sees posts that are not yet in seen.json,
        # so a smaller count means the rewrite would drop earlier matches.
        self.path.write_text("# old\n\nMatches: 3\n", encoding="utf-8")
        self.assertFalse(lw.write_report(self.path, "# new\n\nMatches: 1\n", 1))
        self.assertIn("Matches: 3", self.path.read_text(encoding="utf-8"))

    def test_an_equal_match_count_may_overwrite(self):
        self.path.write_text("# old\n\nMatches: 2\n", encoding="utf-8")
        self.assertTrue(lw.write_report(self.path, "# new\n\nMatches: 2\n", 2))

    def test_empty_report_may_overwrite_another_empty_one(self):
        self.path.write_text("# old\n\nMatches: 0\n", encoding="utf-8")
        self.assertTrue(lw.write_report(self.path, "# new\n\nMatches: 0\n", 0))

    def test_header_records_transport_and_coverage(self):
        report = lw.render_report([], "2026-08-08", "feed", 7, "2026-08-01T00:00:00Z", ["a note"])
        self.assertIn("Transport: feed", report)
        self.assertIn("Posts fetched: 7 (oldest: 2026-08-01T00:00:00Z)", report)
        self.assertIn("- a note", report)

    def test_missing_base_score_renders_as_na(self):
        match = lw.classify(lw.parse_feed(feed_xml("id0"), "rss")[0])
        self.assertIn("Karma/baseScore: n/a", lw.render_report([match], "2026-08-08", "feed", 1, None))


class WindowTests(unittest.TestCase):
    def test_stale_posts_are_dropped_client_side(self):
        posts = [
            {"id": "fresh", "postedAt": "2026-08-07T10:00:00+00:00"},
            {"id": "stale", "postedAt": "2026-06-01T10:00:00+00:00"},
        ]
        kept = lw.within_window(posts, "2026-07-25T00:00:00+00:00")
        self.assertEqual([p["id"] for p in kept], ["fresh"])

    def test_an_unparseable_date_is_kept(self):
        posts = [{"id": "odd", "postedAt": "not a date"}, {"id": "none", "postedAt": None}]
        self.assertEqual(len(lw.within_window(posts, "2026-07-25T00:00:00+00:00")), 2)

    def test_graphql_z_suffix_is_understood(self):
        posts = [{"id": "z", "postedAt": "2026-08-07T10:00:00.000Z"}]
        self.assertEqual(len(lw.within_window(posts, "2026-07-25T00:00:00+00:00")), 1)
        self.assertEqual(len(lw.within_window(posts, "2026-08-08T00:00:00+00:00")), 0)


class MainTests(NoSleepTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.out_dir = root / "reports"
        self.seen_file = root / "seen.json"
        self.seen_file.write_text(json.dumps(["already-seen"]), encoding="utf-8")
        for patch in (
            mock.patch.object(lw, "OUT_DIR", self.out_dir),
            mock.patch.object(lw, "SEEN_FILE", self.seen_file),
            mock.patch.object(lw, "POST_SCOPE", "all"),
            mock.patch.object(lw, "BLOCK_STRIKES", 2),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _run_with(self, responses):
        session = FakeSession(responses)
        with mock.patch.object(lw, "make_session", return_value=session):
            return lw.main()

    def test_strict_failure_exits_nonzero_and_writes_nothing(self):
        blocked = [FakeResponse(429, headers={"content-type": "text/html"})] * 4
        with mock.patch.object(lw, "STRICT", True), mock.patch.object(lw, "TRANSPORT", "auto"):
            self.assertEqual(self._run_with(blocked), 1)
        self.assertEqual(list(self.out_dir.glob("*.md")), [])
        self.assertEqual(json.loads(self.seen_file.read_text(encoding="utf-8")), ["already-seen"])

    def test_non_strict_failure_degrades_to_a_noted_report(self):
        blocked = [FakeResponse(429, headers={"content-type": "text/html"})] * 4
        with mock.patch.object(lw, "STRICT", False), mock.patch.object(lw, "TRANSPORT", "auto"):
            self.assertEqual(self._run_with(blocked), 0)
        report = next(self.out_dir.glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("Could not fetch posts", report)
        self.assertIn("Matches: 0", report)

    def test_happy_path_writes_a_report_and_records_seen_ids(self):
        payload = graphql_payload([gql_post("new-id")], total=1)
        with mock.patch.object(lw, "TRANSPORT", "graphql"), mock.patch.object(lw, "POST_LIMIT", 10):
            self.assertEqual(self._run_with([FakeResponse(json_data=payload)]), 0)
        report = next(self.out_dir.glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("Matches: 1", report)
        self.assertIn("Transport: graphql", report)
        self.assertEqual(
            sorted(json.loads(self.seen_file.read_text(encoding="utf-8"))),
            ["already-seen", "new-id"],
        )

    def test_a_body_less_post_is_not_burned_in_seen_json(self):
        payload = graphql_payload([gql_post("ghost", html="")], total=1)
        with mock.patch.object(lw, "TRANSPORT", "graphql"), mock.patch.object(lw, "POST_LIMIT", 10):
            self.assertEqual(self._run_with([FakeResponse(json_data=payload)]), 0)
        self.assertEqual(json.loads(self.seen_file.read_text(encoding="utf-8")), ["already-seen"])
        report = next(self.out_dir.glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("without body content", report)

    def test_a_same_day_rerun_accumulates_matches_instead_of_replacing_them(self):
        run1 = graphql_payload([gql_post("A", title="POST A")], total=1)
        run2 = graphql_payload([gql_post("A", title="POST A"), gql_post("B", title="POST B")], total=2)
        with mock.patch.object(lw, "TRANSPORT", "graphql"), mock.patch.object(lw, "POST_LIMIT", 10):
            self.assertEqual(self._run_with([FakeResponse(json_data=run1)]), 0)
            # Run 2 only classifies B — A is already in seen.json — so a
            # from-scratch rewrite would silently delete A.
            self.assertEqual(self._run_with([FakeResponse(json_data=run2)]), 0)
        report = next(self.out_dir.glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("Matches: 2", report)
        self.assertIn("POST A", report)
        self.assertIn("POST B", report)

    def test_a_refused_write_does_not_burn_the_matched_ids(self):
        # A report with no match index alongside it (e.g. written before the
        # index existed) must not have its entries dropped.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / f"{today}.md").write_text("# old\n\nMatches: 3\n", encoding="utf-8")
        payload = graphql_payload([gql_post("NEW")], total=1)
        with mock.patch.object(lw, "TRANSPORT", "graphql"), mock.patch.object(lw, "POST_LIMIT", 10):
            self.assertEqual(self._run_with([FakeResponse(json_data=payload)]), 1)
        self.assertIn("Matches: 3", (self.out_dir / f"{today}.md").read_text(encoding="utf-8"))
        # NEW matched but reached no report, so it must stay eligible.
        self.assertNotIn("NEW", json.loads(self.seen_file.read_text(encoding="utf-8")))

    def test_a_rejected_post_is_still_marked_seen_when_a_write_is_refused(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / f"{today}.md").write_text("# old\n\nMatches: 3\n", encoding="utf-8")
        payload = graphql_payload(
            [gql_post("NEW"), gql_post("SHORT", html="<p>short</p>", word_count=10)], total=2
        )
        with mock.patch.object(lw, "TRANSPORT", "graphql"), mock.patch.object(lw, "POST_LIMIT", 10):
            self._run_with([FakeResponse(json_data=payload)])
        seen = json.loads(self.seen_file.read_text(encoding="utf-8"))
        self.assertIn("SHORT", seen)
        self.assertNotIn("NEW", seen)

    def test_a_rerun_cannot_clobber_a_good_report_with_an_empty_one(self):
        good = graphql_payload([gql_post("new-id")], total=1)
        with mock.patch.object(lw, "TRANSPORT", "graphql"), mock.patch.object(lw, "POST_LIMIT", 10):
            self._run_with([FakeResponse(json_data=good)])
            # Second run: the post is now in seen.json, so it yields no matches.
            self._run_with([FakeResponse(json_data=good)])
        report = next(self.out_dir.glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("Matches: 1", report)


if __name__ == "__main__":
    unittest.main()
