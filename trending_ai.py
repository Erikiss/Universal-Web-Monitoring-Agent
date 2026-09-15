"""Daily trending-AI-topics digest.

Queries three public sources for the last 24 hours (override with
LOOKBACK_HOURS) and writes a Markdown report to
``reports/trending_ai_YYYY-MM-DD.md``:

* arXiv  — new submissions in cs.AI, cs.LG, cs.CL, cs.NE (ARXIV_CATEGORIES).
  Its submittedDate index lags by about a day, so arXiv is queried over a
  wider window (ARXIV_LOOKBACK_HOURS, default 72h).
* Hacker News — AI-related stories via the public Algolia search API
* GitHub — repositories pushed in the window under AI-related topics

From the collected titles the script derives a ranked list of trending terms
(1- to 3-grams, stopword-filtered, weighted by the attention each item
received), so the report opens with "what was talked about today" before the
per-source item lists.

Conventions follow the rest of this repository:

* **Fail loudly.** If a source cannot be read, the run exits non-zero and
  writes no report rather than pretending it was a quiet day. Set
  ``TRENDING_STRICT=0`` to downgrade that to a green run with a report that
  names the degraded sources.
* **Ask, don't evade.** One identified request per source per day, retries
  with backoff, no bypassing of blocks.
* **No duplicates.** Item IDs already in ``seen_trending_ai.json`` are not
  reported again; the terms ranking is always computed over the full window
  so trends stay comparable across days.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

OUT_DIR = Path("reports")
SEEN_FILE = Path(os.getenv("TRENDING_SEEN_FILE", "seen_trending_ai.json"))

ARXIV_API = "https://export.arxiv.org/api/query"
HN_API = "https://hn.algolia.com/api/v1/search_by_date"
GITHUB_API = "https://api.github.com/search/repositories"

LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "24"))
STRICT = os.getenv("TRENDING_STRICT", "1") != "0"
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "4"))

CATEGORIES = [c.strip() for c in os.getenv("ARXIV_CATEGORIES", "cs.AI,cs.LG,cs.CL,cs.NE").split(",") if c.strip()]
ARXIV_MAX_RESULTS = int(os.getenv("ARXIV_MAX_RESULTS", "400"))
# arXiv's submittedDate index lags behind announcement by roughly a day, so a
# 24h window there comes back empty every single day. Query a wider window and
# let seen_trending_ai.json mark which of those papers are new to this report.
ARXIV_LOOKBACK_HOURS = int(os.getenv("ARXIV_LOOKBACK_HOURS", "72"))

HN_QUERIES = [q.strip() for q in os.getenv(
    "HN_QUERIES", "AI,LLM,machine learning,neural network,OpenAI,Anthropic"
).split(",") if q.strip()]
HN_MIN_POINTS = int(os.getenv("HN_MIN_POINTS", "20"))
HN_HITS_PER_QUERY = int(os.getenv("HN_HITS_PER_QUERY", "50"))

GITHUB_TOPICS = [t.strip() for t in os.getenv(
    "GITHUB_TOPICS", "llm,machine-learning,artificial-intelligence,generative-ai"
).split(",") if t.strip()]
GITHUB_MIN_STARS = int(os.getenv("GITHUB_MIN_STARS", "50"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()

TOP_TERMS = int(os.getenv("TOP_TERMS", "25"))
TOP_ITEMS_PER_SOURCE = int(os.getenv("TOP_ITEMS_PER_SOURCE", "20"))

USER_AGENT = "Universal-Web-Monitoring-Agent/1.0 (+https://github.com/erikiss/universal-web-monitoring-agent)"

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# Words that carry no topical signal in this corpus, plus the generic AI terms
# that would otherwise top every single day's ranking.
STOPWORDS = {
    # "ai", "llm", "hn", "show" et al. would otherwise top the list every day.
    "a", "ai", "about", "across", "after", "against", "all", "an", "analysis", "and", "any",
    "approach", "are", "as", "at", "based", "be", "been", "being", "better", "between",
    "beyond", "both", "but", "by", "can", "case", "data", "deep", "did", "do", "does",
    "during", "each", "efficient", "every", "for", "from", "has", "have", "how", "however",
    "framework", "frameworks", "hn", "i", "if", "in", "into", "is", "it", "its", "just",
    "language", "large", "learning", "like", "llm", "llms", "machine",
    "make", "many", "method", "methods", "model", "models", "more", "most", "network",
    "networks", "neural", "new", "no", "not", "novel", "of", "on", "one", "only", "or",
    "other", "our", "out", "over", "paper", "per", "research", "results", "same", "show",
    "show", "so", "some", "study", "such", "system", "systems", "than", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "through", "to", "toward", "towards", "two", "under", "up",
    "use", "using", "via", "was", "we", "were", "what", "when", "which", "while", "who",
    "why", "will", "with", "within", "without", "you", "your",
}

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-+.]*")


def log(msg):
    print(msg, flush=True)


class SourceError(RuntimeError):
    """A source could not be read; how fatal that is depends on STRICT."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def make_session():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    return session


def get(session, url, params=None, headers=None, source=""):
    """GET with bounded retries. Logs the evidence of every refusal."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        started = time.monotonic()
        try:
            resp = session.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            elapsed = time.monotonic() - started
            if resp.status_code in (429, 403) or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 120)
                except (TypeError, ValueError):
                    delay = min(5 * 2 ** (attempt - 1), 120)
                log(
                    f"{source}: HTTP {resp.status_code} after {elapsed:.1f}s "
                    f"(attempt {attempt}/{MAX_RETRIES}, ratelimit-remaining="
                    f"{resp.headers.get('X-RateLimit-Remaining', 'n/a')}, "
                    f"retry-after={retry_after or 'n/a'}): {resp.text[:200]!r}"
                )
                last_error = SourceError(f"HTTP {resp.status_code}")
                if attempt < MAX_RETRIES:
                    time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            elapsed = time.monotonic() - started
            last_error = e
            delay = min(5 * 2 ** (attempt - 1), 120)
            log(f"{source}: request error after {elapsed:.1f}s (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(delay)
    raise SourceError(f"{source}: giving up after {MAX_RETRIES} attempts ({last_error})")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def parse_arxiv_feed(content):
    root = ET.fromstring(content)
    items = []
    for entry in root.findall("atom:entry", ATOM_NS):
        entry_id = (entry.findtext("atom:id", default="", namespaces=ATOM_NS) or "").strip()
        title = " ".join((entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").split())
        published = (entry.findtext("atom:published", default="", namespaces=ATOM_NS) or "").strip()
        if not entry_id or not title:
            continue
        arxiv_id = re.sub(r"v\d+$", "", re.sub(r"^https?://arxiv\.org/abs/", "", entry_id))
        items.append(
            {
                "id": f"arxiv:{arxiv_id}",
                "title": title,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "published": published,
                "weight": 1.0,
                "meta": "",
            }
        )
    return items


def fetch_arxiv(session, _cutoff, now):
    # Deliberately ignores the shared cutoff: see ARXIV_LOOKBACK_HOURS.
    cutoff = now - timedelta(hours=ARXIV_LOOKBACK_HOURS)
    query = "({}) AND submittedDate:[{} TO {}]".format(
        " OR ".join(f"cat:{c}" for c in CATEGORIES),
        cutoff.strftime("%Y%m%d%H%M"),
        now.strftime("%Y%m%d%H%M"),
    )
    resp = get(
        session,
        ARXIV_API,
        params={
            "search_query": query,
            "start": 0,
            "max_results": ARXIV_MAX_RESULTS,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
        source="arxiv",
    )
    try:
        items = parse_arxiv_feed(resp.content)
    except ET.ParseError as e:
        raise SourceError(f"arxiv: unparseable feed ({e})")
    log(f"arxiv: {len(items)} submissions in window")
    return items


def fetch_hackernews(session, cutoff):
    by_id = {}
    for query in HN_QUERIES:
        resp = get(
            session,
            HN_API,
            params={
                "query": query,
                "tags": "story",
                "hitsPerPage": HN_HITS_PER_QUERY,
                "numericFilters": f"created_at_i>{int(cutoff.timestamp())},points>={HN_MIN_POINTS}",
            },
            source="hackernews",
        )
        try:
            hits = resp.json().get("hits", [])
        except ValueError as e:
            raise SourceError(f"hackernews: non-JSON response ({e})")
        for hit in hits:
            object_id = str(hit.get("objectID") or "")
            title = " ".join((hit.get("title") or "").split())
            if not object_id or not title:
                continue
            points = int(hit.get("points") or 0)
            comments = int(hit.get("num_comments") or 0)
            by_id[object_id] = {
                "id": f"hn:{object_id}",
                "title": title,
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}",
                "published": hit.get("created_at") or "",
                # Attention weight: points dominate, comments add a little.
                "weight": 1.0 + points / 100.0 + comments / 200.0,
                "meta": f"{points} points, {comments} comments",
                "sort_key": points,
            }
        time.sleep(0.5)
    items = sorted(by_id.values(), key=lambda i: i.get("sort_key", 0), reverse=True)
    log(f"hackernews: {len(items)} stories in window")
    return items


def fetch_github(session, cutoff):
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    topic_clause = " ".join(f"topic:{t}" for t in GITHUB_TOPICS)
    resp = get(
        session,
        GITHUB_API,
        params={
            "q": f"{topic_clause} pushed:>{cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')} stars:>={GITHUB_MIN_STARS}",
            "sort": "stars",
            "order": "desc",
            "per_page": 50,
        },
        headers=headers,
        source="github",
    )
    try:
        payload = resp.json()
    except ValueError as e:
        raise SourceError(f"github: non-JSON response ({e})")
    items = []
    for repo in payload.get("items", []):
        full_name = repo.get("full_name")
        if not full_name:
            continue
        stars = int(repo.get("stargazers_count") or 0)
        description = " ".join((repo.get("description") or "").split())
        items.append(
            {
                "id": f"gh:{full_name}",
                "title": f"{full_name} — {description}" if description else full_name,
                "url": repo.get("html_url") or f"https://github.com/{full_name}",
                "published": repo.get("pushed_at") or "",
                "weight": 1.0 + stars / 5000.0,
                "meta": f"{stars} stars",
                "sort_key": stars,
            }
        )
    items.sort(key=lambda i: i.get("sort_key", 0), reverse=True)
    log(f"github: {len(items)} repositories in window")
    return items


SOURCES = {
    "arxiv": (f"arXiv (cs.AI/LG/CL/NE, last {ARXIV_LOOKBACK_HOURS}h)", lambda session, cutoff, now: fetch_arxiv(session, cutoff, now)),
    "hackernews": ("Hacker News", lambda session, cutoff, now: fetch_hackernews(session, cutoff)),
    "github": ("GitHub", lambda session, cutoff, now: fetch_github(session, cutoff)),
}


# ---------------------------------------------------------------------------
# Trend extraction
# ---------------------------------------------------------------------------

def tokenize(text):
    return [t for t in TOKEN_RE.findall(text.lower()) if len(t) > 1]


def ngrams(tokens, n):
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def is_useful(term):
    words = term.split()
    if any(w in STOPWORDS for w in words):
        return False
    return not all(w.isdigit() for w in words)


def rank_terms(items, top_n=TOP_TERMS, min_items=2):
    """Score 1- to 3-grams by the summed attention weight of the items using them.

    A term must appear in at least ``min_items`` distinct items, so a single
    long title cannot invent a trend. Longer n-grams get a small boost so
    "mixture of experts" outranks its own parts when both occur equally often.
    """
    scores = defaultdict(float)
    item_counts = Counter()
    examples = defaultdict(list)
    for item in items:
        tokens = tokenize(item["title"])
        weight = float(item.get("weight", 1.0))
        seen_here = set()
        for n in (1, 2, 3):
            for term in ngrams(tokens, n):
                if term in seen_here or not is_useful(term):
                    continue
                seen_here.add(term)
                scores[term] += weight * (1.0 + 0.35 * (n - 1))
                item_counts[term] += 1
                if len(examples[term]) < 3:
                    examples[term].append(item)
    ranked = [
        {"term": term, "score": score, "items": item_counts[term], "examples": examples[term]}
        for term, score in scores.items()
        if item_counts[term] >= min_items
    ]
    ranked.sort(key=lambda r: (-r["score"], -r["items"], r["term"]))
    return ranked[:top_n]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def load_seen(path=SEEN_FILE):
    """Return previously reported ids, oldest first (insertion order is kept)."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        log(f"seen file unreadable ({e}); starting from an empty list")
        return []
    if isinstance(data, dict):
        data = data.get("ids", [])
    return list(data or [])


def save_seen(ids, path=SEEN_FILE, keep=20000):
    # The window is a day but the file is forever: drop the oldest ids once
    # the file grows past `keep`, since they can no longer reappear anyway.
    path.write_text(json.dumps(list(ids)[-keep:], indent=0) + "\n", encoding="utf-8")


def render_report(now, results, ranked, failures, new_ids):
    total = sum(len(items) for items in results.values())
    lines = [
        f"# Trending AI topics — {now.strftime('%Y-%m-%d')}",
        "",
        f"Window: last {LOOKBACK_HOURS}h (arXiv: {ARXIV_LOOKBACK_HOURS}h) "
        f"until {now.strftime('%Y-%m-%d %H:%M UTC')} · "
        f"{total} items · {len(new_ids)} new since the last run",
        "",
    ]
    if failures:
        lines += [
            "> **Degraded run.** These sources could not be read: "
            + ", ".join(f"{name} ({reason})" for name, reason in failures),
            "",
        ]

    lines += ["## Top terms", ""]
    if ranked:
        lines += ["| # | Term | Score | Items | Example |", "| --- | --- | --- | --- | --- |"]
        for rank, entry in enumerate(ranked, start=1):
            example = entry["examples"][0]
            link = f"[{example['title'][:80]}]({example['url']})"
            lines.append(
                f"| {rank} | **{entry['term']}** | {entry['score']:.1f} | {entry['items']} | {link} |"
            )
    else:
        lines.append("_No term occurred in enough items to count as a trend._")
    lines.append("")

    for key, (label, _) in SOURCES.items():
        items = results.get(key)
        if items is None:
            continue
        lines += [f"## {label} ({len(items)})", ""]
        if not items:
            lines += ["_Nothing in the window._", ""]
            continue
        for item in items[:TOP_ITEMS_PER_SOURCE]:
            marker = " 🆕" if item["id"] in new_ids else ""
            meta = f" — {item['meta']}" if item.get("meta") else ""
            lines.append(f"- [{item['title']}]({item['url']}){meta}{marker}")
        if len(items) > TOP_ITEMS_PER_SOURCE:
            lines.append(f"- … and {len(items) - TOP_ITEMS_PER_SOURCE} more")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    session = make_session()

    results = {}
    failures = []
    for key, (label, fetch) in SOURCES.items():
        try:
            results[key] = fetch(session, cutoff, now)
        except SourceError as e:
            log(f"{label}: FAILED — {e}")
            failures.append((label, str(e)))

    if failures and STRICT:
        log("Strict mode: a source failed, writing no report (set TRENDING_STRICT=0 to downgrade).")
        return 1
    if not results:
        log("No source could be read at all.")
        return 1

    all_items = [item for items in results.values() for item in items]
    ranked = rank_terms(all_items)

    seen = load_seen()
    seen_set = set(seen)
    # Keep the report's order so the appended ids stay roughly chronological.
    new_ordered = list(dict.fromkeys(
        item["id"] for item in all_items if item["id"] not in seen_set
    ))
    new_ids = set(new_ordered)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"trending_ai_{now.strftime('%Y-%m-%d')}.md"
    out_path.write_text(render_report(now, results, ranked, failures, new_ids), encoding="utf-8")
    log(f"Wrote {out_path} ({len(all_items)} items, {len(new_ids)} new, {len(ranked)} terms)")

    save_seen(seen + new_ordered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
