"""Daily trending-AI-topics digest.

Queries three public sources for the last 24 hours (override with
LOOKBACK_HOURS) and writes a Markdown report to
``reports/trending_ai_YYYY-MM-DD.md``:

* arXiv  — new submissions in cs.AI, cs.LG, cs.CL, cs.NE (ARXIV_CATEGORIES).
  Its submittedDate index lags by about a day, so arXiv is queried over a
  wider window (ARXIV_LOOKBACK_HOURS, default 72h).
* Hacker News — AI-related stories via the public Algolia search API
* GitHub — repositories pushed in the window under AI-related topics
* OpenAlex — new works from a configured list of institutions matching the
  configured search terms (opt-in, see OPENALEX_MAILTO / OPENALEX_API_KEY).
  Its hits are additionally exported as ``reports/openalex_YYYY-MM-DD.csv``
  with the same columns the Colab notebook produced.

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

import csv
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
OPENALEX_API = "https://api.openalex.org/works"

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

# OpenAlex institution watchlist (OpenAlex IDs), mirroring the notebook this
# job replaces. Format: "Label=I12345678,Other=I87654321".
OPENALEX_INSTITUTIONS = [
    tuple(part.split("=", 1)) if "=" in part else (part, part)
    for part in (
        p.strip()
        for p in os.getenv(
            "OPENALEX_INSTITUTIONS",
            "Yale=I32971472,Princeton=I20089843,Stanford=I97018004,MIT=I63966007,"
            "Harvard=I136199984,Oxford=I40120149,ETH Zurich=I35440088",
        ).split(",")
    )
    if part
]
OPENALEX_SEARCHES = [s.strip() for s in os.getenv(
    "OPENALEX_SEARCHES", "machine unlearning,large language model,artificial intelligence"
).split(",") if s.strip()]
OPENALEX_LOOKBACK_DAYS = int(os.getenv("OPENALEX_LOOKBACK_DAYS", "7"))
OPENALEX_PER_PAGE = int(os.getenv("OPENALEX_PER_PAGE", "50"))
# OpenAlex meters requests per caller: unidentified traffic from a shared IP
# (a GitHub runner) is answered with "Insufficient budget". The source is
# therefore opt-in — set a contact address for the polite pool, or an API key.
OPENALEX_MAILTO = os.getenv("OPENALEX_MAILTO", "").strip()
OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "").strip()

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
                detail = ""
                if "Insufficient budget" in resp.text:
                    # OpenAlex meters per caller; retrying cannot help today.
                    detail = " — OpenAlex budget exhausted until midnight UTC; " \
                             "configure OPENALEX_MAILTO/OPENALEX_API_KEY or raise the budget"
                last_error = SourceError(f"HTTP {resp.status_code}{detail}")
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


def openalex_enabled():
    return bool(OPENALEX_MAILTO or OPENALEX_API_KEY)


def openalex_record(work, institution_query, institution_name, institution_id):
    """Flatten an OpenAlex work into the CSV row layout of the notebook export."""
    location = ((work.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
    return {
        "institution_query": institution_query,
        "institution": institution_name,
        "institution_openalex_id": institution_id,
        "publication_date": work.get("publication_date") or "",
        "title": " ".join((work.get("display_name") or "").split()),
        "type": work.get("type") or "",
        "openalex_id": work.get("id") or "",
        "doi": work.get("doi") or "",
        "primary_location": location,
        "cited_by_count": work.get("cited_by_count") or 0,
        "openalex_url": work.get("id") or "",
    }


def fetch_openalex(session, _cutoff, now):
    """New works from the watched institutions, one request per search term.

    All institutions go into a single `institutions.id` OR-filter, so the
    request budget scales with the number of search terms, not with the
    watchlist.
    """
    ids_by_id = {inst_id: (label, inst_id) for label, inst_id in OPENALEX_INSTITUTIONS}
    id_filter = "|".join(ids_by_id)
    from_date = (now - timedelta(days=OPENALEX_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    records = {}
    items = {}
    for search in OPENALEX_SEARCHES:
        params = {
            "filter": f"institutions.id:{id_filter},from_publication_date:{from_date}",
            "search": search,
            "per-page": OPENALEX_PER_PAGE,
            "sort": "publication_date:desc",
        }
        if OPENALEX_MAILTO:
            params["mailto"] = OPENALEX_MAILTO
        if OPENALEX_API_KEY:
            params["api_key"] = OPENALEX_API_KEY
        resp = get(session, OPENALEX_API, params=params, source="openalex")
        try:
            results = resp.json().get("results", [])
        except ValueError as e:
            raise SourceError(f"openalex: non-JSON response ({e})")
        for work in results:
            work_id = (work.get("id") or "").rsplit("/", 1)[-1]
            title = " ".join((work.get("display_name") or "").split())
            if not work_id or not title:
                continue
            # A work can be authored at several watched institutions; the CSV
            # keeps one row per (institution, work) exactly as the notebook did.
            for inst in (
                institution
                for authorship in (work.get("authorships") or [])
                for institution in (authorship.get("institutions") or [])
            ):
                inst_id = (inst.get("id") or "").rsplit("/", 1)[-1]
                if inst_id not in ids_by_id:
                    continue
                label, _ = ids_by_id[inst_id]
                records[(inst_id, work_id)] = openalex_record(
                    work, label, inst.get("display_name") or label, inst.get("id") or inst_id
                )
            cited = int(work.get("cited_by_count") or 0)
            items[work_id] = {
                "id": f"openalex:{work_id}",
                "title": title,
                "url": work.get("doi") or work.get("id") or "",
                "published": work.get("publication_date") or "",
                "weight": 1.0 + cited / 50.0,
                "meta": f"{search} · {work.get('type') or 'work'}",
                "sort_key": cited,
            }
        time.sleep(1.0)

    write_openalex_csv(now, list(records.values()))
    ordered = sorted(items.values(), key=lambda i: i.get("sort_key", 0), reverse=True)
    log(f"openalex: {len(ordered)} works in window ({len(records)} institution rows)")
    return ordered


OPENALEX_CSV_COLUMNS = [
    "institution_query", "institution", "institution_openalex_id", "publication_date",
    "title", "type", "openalex_id", "doi", "primary_location", "cited_by_count",
    "openalex_url",
]


def write_openalex_csv(now, records):
    """Write the institution rows in the notebook's CSV layout."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"openalex_{now.strftime('%Y-%m-%d')}.csv"
    records = sorted(
        records,
        key=lambda r: (r["institution_query"], r["publication_date"]),
        reverse=False,
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OPENALEX_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(records)
    log(f"Wrote {path} ({len(records)} rows)")
    return path


SOURCES = {
    "arxiv": (f"arXiv (cs.AI/LG/CL/NE, last {ARXIV_LOOKBACK_HOURS}h)", lambda session, cutoff, now: fetch_arxiv(session, cutoff, now)),
    "hackernews": ("Hacker News", lambda session, cutoff, now: fetch_hackernews(session, cutoff)),
    "github": ("GitHub", lambda session, cutoff, now: fetch_github(session, cutoff)),
    "openalex": (
        f"OpenAlex institutions (last {OPENALEX_LOOKBACK_DAYS}d)",
        fetch_openalex,
    ),
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
        if key == "openalex" and not openalex_enabled():
            # Not a failure: OpenAlex meters unidentified traffic, so the
            # source stays off until a contact address or key is configured.
            log(
                "openalex: skipped — set OPENALEX_MAILTO (polite pool) or "
                "OPENALEX_API_KEY to enable it"
            )
            continue
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
