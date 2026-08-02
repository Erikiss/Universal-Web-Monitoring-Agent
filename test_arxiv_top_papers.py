"""Offline unit tests for arxiv_top_papers.py — all HTTP is mocked, so the
suite runs without network access: python -m unittest test_arxiv_top_papers.py
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import requests

import arxiv_top_papers as atp

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)

ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>{total}</opensearch:totalResults>
  {entries}
</feed>
"""

ENTRY_TEMPLATE = """<entry>
  <id>http://arxiv.org/abs/{arxiv_id}v1</id>
  <title>{title}</title>
  <summary>Some abstract.</summary>
  <published>{published}</published>
  <author><name>Alice Example</name></author>
  <author><name>Bob Example</name></author>
  <arxiv:primary_category term="cs.LG"/>
  <category term="cs.LG"/>
  <category term="cs.AI"/>
</entry>"""


def make_feed(specs, total=None):
    entries = "\n".join(
        ENTRY_TEMPLATE.format(arxiv_id=a, title=t, published=p) for a, t, p in specs
    )
    return ATOM_FEED.format(total=total if total is not None else len(specs), entries=entries).encode()


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b"", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.content = content if content else (json.dumps(payload).encode() if payload is not None else b"")
        self.headers = headers or {}

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    """Routes requests to programmable handlers and records every call."""

    def __init__(self):
        self.handlers = []
        self.calls = []
        self.headers = {}

    def add(self, matcher, response):
        self.handlers.append((matcher, response))

    def _dispatch(self, method, url, params, json_body, headers=None):
        self.calls.append({"method": method, "url": url, "params": params,
                           "json": json_body, "headers": dict(headers or {})})
        for matcher, response in self.handlers:
            if matcher(method, url, params or {}, json_body):
                return response(method, url, params or {}, json_body) if callable(response) else response
        raise AssertionError(f"unexpected request: {method} {url} {params}")

    def get(self, url, params=None, timeout=None):
        return self._dispatch("GET", url, params, None)

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        return self._dispatch(method, url, params, json, headers)


def no_sleep_client(session):
    client = atp.S2Client(session)
    client._throttle = lambda: None
    return client


class BareArxivIdTest(unittest.TestCase):
    def test_new_style_with_version(self):
        self.assertEqual(atp.bare_arxiv_id("http://arxiv.org/abs/2507.12345v2"), "2507.12345")

    def test_https_and_no_version(self):
        self.assertEqual(atp.bare_arxiv_id("https://arxiv.org/abs/2507.12345"), "2507.12345")

    def test_old_style_id(self):
        self.assertEqual(atp.bare_arxiv_id("http://arxiv.org/abs/cs/0112017v1"), "cs/0112017")


class ParseArxivFeedTest(unittest.TestCase):
    def test_parses_entries(self):
        feed = make_feed([("2508.00001", "Paper A | with pipe", "2026-08-01T10:00:00Z")], total=42)
        total, papers = atp.parse_arxiv_feed(feed)
        self.assertEqual(total, 42)
        self.assertEqual(len(papers), 1)
        p = papers[0]
        self.assertEqual(p["arxiv_id"], "2508.00001")
        self.assertEqual(p["title"], "Paper A | with pipe")
        self.assertEqual(p["url"], "https://arxiv.org/abs/2508.00001v1")
        self.assertEqual(p["authors"], ["Alice Example", "Bob Example"])
        self.assertEqual(p["primary_category"], "cs.LG")
        self.assertEqual(p["categories"], ["cs.LG", "cs.AI"])


class S2AuthFallbackTest(unittest.TestCase):
    def test_invalid_key_falls_back_to_unauthenticated(self):
        session = FakeSession()
        responses = [FakeResponse(403, content=b"Forbidden"),
                     FakeResponse(200, payload={"ok": True})]
        session.add(lambda m, u, p, j: True, lambda m, u, p, j: responses.pop(0))
        with mock.patch.object(atp, "S2_API_KEY", "bad-key"):
            client = no_sleep_client(session)
        with mock.patch.object(atp.time, "sleep"):
            self.assertEqual(client.request("GET", "https://example/x"), {"ok": True})
        self.assertIn("x-api-key", session.calls[0]["headers"])
        self.assertNotIn("x-api-key", session.calls[1]["headers"])
        self.assertGreaterEqual(client.delay, 3.0)  # unauthenticated pacing

    def test_403_without_key_raises_actionable_error(self):
        session = FakeSession()
        session.add(lambda m, u, p, j: True, FakeResponse(403, content=b"Forbidden"))
        client = no_sleep_client(session)
        with self.assertRaises(atp.S2AuthError) as ctx:
            client.request("GET", "https://example/x")
        self.assertIn("S2_API_KEY", str(ctx.exception))
        self.assertEqual(len(session.calls), 1)  # no pointless retries

    def test_batch_403_does_not_split(self):
        session = FakeSession()
        session.add(batch_matcher, FakeResponse(403, content=b"Forbidden"))
        with self.assertRaises(atp.S2AuthError):
            atp.fetch_s2_records(no_sleep_client(session), [f"2508.{i:05d}" for i in range(60)])
        self.assertEqual(len(session.calls), 1)  # one rejected batch, no split cascade


class MdEscapeTest(unittest.TestCase):
    def test_escapes_link_breaking_characters(self):
        self.assertEqual(atp.md_escape("A [B] C| \\D"), "A \\[B\\] C\\| \\\\D")


class FetchArxivPageRetryTest(unittest.TestCase):
    def test_retries_transient_total_zero_page(self):
        session = FakeSession()
        responses = [
            FakeResponse(200, content=make_feed([], total=0)),
            FakeResponse(200, content=make_feed([("2508.00001", "A", "2026-08-01T10:00:00Z")], total=1)),
        ]
        session.add(lambda m, u, p, j: True, lambda m, u, p, j: responses.pop(0))
        with mock.patch.object(atp.time, "sleep"):
            total, entries = atp.fetch_arxiv_page(session, "q", 0)
        self.assertEqual(total, 1)
        self.assertEqual(len(entries), 1)

    def test_known_total_forces_retry_then_fails_loudly(self):
        session = FakeSession()
        session.add(lambda m, u, p, j: True, FakeResponse(200, content=make_feed([], total=0)))
        with mock.patch.object(atp.time, "sleep"), self.assertRaises(RuntimeError):
            atp.fetch_arxiv_page(session, "q", 1000, known_total=1500)
        self.assertEqual(len(session.calls), atp.MAX_RETRIES)

    def test_accepts_genuinely_empty_window(self):
        session = FakeSession()
        session.add(lambda m, u, p, j: True, FakeResponse(200, content=make_feed([], total=0)))
        with mock.patch.object(atp.time, "sleep"):
            total, entries = atp.fetch_arxiv_page(session, "q", 0)
        self.assertEqual((total, entries), (0, []))
        self.assertEqual(len(session.calls), 3)  # retried twice before accepting


class S2ClientRetryTest(unittest.TestCase):
    def test_retries_429_then_succeeds(self):
        session = FakeSession()
        responses = [FakeResponse(429, headers={"Retry-After": "0"}), FakeResponse(200, payload={"ok": True})]
        session.add(lambda m, u, p, j: True, lambda m, u, p, j: responses.pop(0))
        client = no_sleep_client(session)
        with mock.patch.object(atp.time, "sleep"):
            self.assertEqual(client.request("GET", "https://example/x"), {"ok": True})
        self.assertEqual(len(session.calls), 2)

    def test_raises_on_persistent_400(self):
        session = FakeSession()
        session.add(lambda m, u, p, j: True, FakeResponse(400))
        client = no_sleep_client(session)
        with self.assertRaises(requests.HTTPError):
            client.request("GET", "https://example/x")
        self.assertEqual(len(session.calls), 1)  # 4xx must not be retried


def batch_matcher(m, u, p, j):
    return m == "POST" and u.endswith("/paper/batch")


def refs_matcher(arxiv_id):
    return lambda m, u, p, j: m == "GET" and u.endswith(f"/paper/arXiv:{arxiv_id}/references")


def s2_paper(arxiv_id, ref_citations=None, reference_count=None, citation_count=0):
    item = {
        "paperId": f"s2-{arxiv_id}",
        "title": f"Title {arxiv_id}",
        "externalIds": {"ArXiv": arxiv_id},
        "referenceCount": reference_count if reference_count is not None else len(ref_citations or []),
        "citationCount": citation_count,
    }
    if ref_citations is not None:
        item["references"] = [{"citationCount": c} for c in ref_citations]
    return item


class FetchS2RecordsTest(unittest.TestCase):
    def test_full_nested_references(self):
        session = FakeSession()
        session.add(batch_matcher, FakeResponse(200, payload=[s2_paper("2508.00001", [10, 5, 0])]))
        records = atp.fetch_s2_records(no_sleep_client(session), ["2508.00001"])
        self.assertEqual(records["2508.00001"]["ref_citations"], [10, 5, 0])
        self.assertEqual(len(session.calls), 1)

    def test_not_found_paper(self):
        session = FakeSession()
        session.add(batch_matcher, FakeResponse(200, payload=[None]))
        records = atp.fetch_s2_records(no_sleep_client(session), ["2508.00002"])
        self.assertFalse(records["2508.00002"]["found"])

    def test_truncated_nested_list_triggers_fallback(self):
        session = FakeSession()
        session.add(batch_matcher, FakeResponse(
            200, payload=[s2_paper("2508.00003", [1] * 100, reference_count=120)]))
        session.add(refs_matcher("2508.00003"), FakeResponse(
            200, payload={"data": [{"citedPaper": {"citationCount": 2}}] * 120}))
        records = atp.fetch_s2_records(no_sleep_client(session), ["2508.00003"])
        self.assertEqual(records["2508.00003"]["ref_citations"], [2] * 120)

    def test_nested_unsupported_falls_back_to_scalar_and_per_paper(self):
        session = FakeSession()

        def batch_response(m, u, p, j):
            if "references.citationCount" in p.get("fields", ""):
                return FakeResponse(400)
            return FakeResponse(200, payload=[s2_paper("2508.00004", reference_count=30)])

        session.add(batch_matcher, batch_response)
        session.add(refs_matcher("2508.00004"), FakeResponse(
            200, payload={"data": [{"citedPaper": {"citationCount": 3}}] * 30}))
        records = atp.fetch_s2_records(no_sleep_client(session), ["2508.00004"])
        self.assertEqual(records["2508.00004"]["ref_citations"], [3] * 30)

    def test_failed_reference_fallback_keeps_reference_count(self):
        session = FakeSession()
        session.add(batch_matcher, FakeResponse(
            200, payload=[s2_paper("2508.00005", [1] * 100, reference_count=150)]))
        session.add(refs_matcher("2508.00005"), FakeResponse(404))
        records = atp.fetch_s2_records(no_sleep_client(session), ["2508.00005"])
        self.assertIsNone(records["2508.00005"]["ref_citations"])
        self.assertEqual(records["2508.00005"]["reference_count"], 150)


class FetchReferencesPaginationTest(unittest.TestCase):
    def test_follows_next_offset(self):
        session = FakeSession()

        def refs_response(m, u, p, j):
            if p.get("offset", 0) == 0:
                return FakeResponse(200, payload={
                    "data": [{"citedPaper": {"citationCount": 1}}] * 1000, "next": 1000})
            return FakeResponse(200, payload={
                "data": [{"citedPaper": {"citationCount": 7}}] * 5})

        session.add(refs_matcher("2508.00006"), refs_response)
        citations = atp.fetch_references(no_sleep_client(session), "2508.00006")
        self.assertEqual(len(citations), 1005)
        self.assertEqual(sum(citations), 1000 + 35)


class RankingTest(unittest.TestCase):
    def make_paper(self, arxiv_id, title):
        return {"arxiv_id": arxiv_id, "url": f"https://arxiv.org/abs/{arxiv_id}v1",
                "title": title, "published": "2026-08-01T00:00:00Z",
                "authors": ["A"], "primary_category": "cs.LG", "categories": ["cs.LG"]}

    def test_rankings_differ_and_exclude_unknown(self):
        papers = [self.make_paper("1", "Many refs"), self.make_paper("2", "Heavy refs"),
                  self.make_paper("3", "Unknown")]
        records = {
            "1": {"found": True, "reference_count": 130, "citation_count": 0,
                  "ref_citations": [0] * 129 + [50]},
            "2": {"found": True, "reference_count": 120, "citation_count": 0,
                  "ref_citations": [1] * 120},
            "3": {"found": False, "reference_count": None, "citation_count": None,
                  "ref_citations": None},
        }
        measurable, by_refs, by_weight = atp.build_rankings(papers, records)
        self.assertEqual(len(measurable), 2)
        self.assertEqual([e["arxiv_id"] for e in by_refs], ["1", "2"])
        self.assertEqual([e["arxiv_id"] for e in by_weight], ["2", "1"])
        self.assertEqual(by_weight[0]["weighted_score"], 120)
        self.assertEqual(by_refs[0]["weighted_score"], 50)

    def test_top_n_cut(self):
        papers = [self.make_paper(str(i), f"P{i}") for i in range(20)]
        records = {str(i): {"found": True, "reference_count": i, "citation_count": 0,
                            "ref_citations": [1] * i} for i in range(20)}
        with mock.patch.object(atp, "TOP_N", 15):
            _, by_refs, by_weight = atp.build_rankings(papers, records)
        self.assertEqual(len(by_refs), 15)
        self.assertEqual(len(by_weight), 15)
        self.assertEqual(by_refs[0]["ref_count"], 19)


class EndToEndTest(unittest.TestCase):
    def test_main_writes_report(self):
        session = FakeSession()
        feed = make_feed([
            ("2508.00001", "Alpha Paper", "2026-08-01T10:00:00Z"),
            ("2508.00002", "Beta | Survey", "2026-07-30T10:00:00Z"),
            ("2508.00003", "Gamma Fresh", "2026-08-02T01:00:00Z"),
        ])
        session.add(lambda m, u, p, j: u == atp.ARXIV_API, FakeResponse(200, content=feed))
        session.add(batch_matcher, FakeResponse(200, payload=[
            s2_paper("2508.00001", [10, 5, 0], citation_count=1),
            s2_paper("2508.00002", [100, 200, 300, 400], citation_count=2),
            None,
        ]))

        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with mock.patch.object(atp.requests, "Session", return_value=session), \
                     mock.patch.object(atp.time, "sleep"), \
                     mock.patch.object(atp, "LOOKBACK_DAYS", 7), \
                     mock.patch.object(atp, "datetime", wraps=atp.datetime) as dt:
                    dt.now.return_value = NOW
                    dt.fromisoformat = atp.datetime.fromisoformat
                    atp.main()
                report = Path(tmp) / "reports" / "arxiv_top15_2026-08-02.md"
                self.assertTrue(report.exists())
                text = report.read_text(encoding="utf-8")
            finally:
                os.chdir(cwd)

        self.assertIn("# Weekly arXiv AI Top Papers — 2026-08-02", text)
        self.assertIn("**Papers in window**: 3", text)
        self.assertIn("**Found on Semantic Scholar**: 2", text)
        self.assertIn("Top 15 by number of references", text)
        self.assertIn("Top 15 by citation-weighted references", text)
        self.assertIn("Beta \\| Survey", text)  # pipe escaped for the table
        self.assertIn("| 4 | 1,000 |", text)   # Beta: 4 refs, weighted 1000
        self.assertIn("| 3 | 15 |", text)      # Alpha: 3 refs, weighted 15
        self.assertNotIn("Gamma Fresh", text)  # not on S2 -> not rankable


if __name__ == "__main__":
    unittest.main()
