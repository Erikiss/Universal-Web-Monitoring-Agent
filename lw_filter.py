"""Filter recent LessWrong posts for long ML/NLP write-ups with visualizations.

Two transports are available, tried in order when ``LW_TRANSPORT=auto``:

``graphql``
    One POST to ``/graphql``. Carries every field the filter wants
    (``baseScore``, ``wordCount``, ``frontpageDate``, the full post HTML) and
    supports ``offset`` pagination, so a single run can cover the whole
    lookback window. Preferred.
``feed``
    GET ``/feed.xml`` once per RSS view. The feed's ``<description>`` is the
    same ``contents.html`` the GraphQL query requests and its ``<guid>`` is the
    bare post ``_id``, so the filter heuristics and ``seen.json`` work
    unchanged. Degraded: the server hardcodes 10 items per view and omits
    ``baseScore``. Its responses are CDN-cached, so it stays reachable in some
    situations where the uncacheable GraphQL POST does not.

Like ``page_watch.py``, this script fails loudly. When no transport can deliver
posts it logs the diagnostics, writes no report and exits non-zero, instead of
committing a fabricated empty day. Set ``LW_STRICT=0`` for the old degrading
behaviour.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

SITE = "https://www.lesswrong.com"
ENDPOINT = f"{SITE}/graphql"
FEED_ENDPOINT = f"{SITE}/feed.xml"
OUT_DIR = Path("reports")
# Machine-readable index of each day's reported matches. The rendered Markdown
# is the deliverable; this is what lets a second run of the same day render the
# union rather than replace the file.
MATCH_STATE_DIRNAME = ".matches"
SEEN_FILE = Path("seen.json")


def env(name: str, *fallbacks: str, default: str) -> str:
    """First non-empty value among ``name`` and its legacy ``fallbacks``.

    The ``LW_``-prefixed names take precedence; the unprefixed ones are kept
    working because ``MAX_RETRIES`` and ``REQUEST_TIMEOUT`` are generic enough
    that three scripts in this repository read them with different intents.
    """
    for key in (name, *fallbacks):
        value = os.getenv(key)
        if value is not None and value.strip():
            return value.strip()
    return default


def env_int(name: str, *fallbacks: str, default: int) -> int:
    raw = env(name, *fallbacks, default=str(default))
    try:
        return int(raw)
    except ValueError:
        log(f"WARNING: {name}={raw!r} is not an integer; using {default}")
        return default


def env_flag(name: str, *, default: bool) -> bool:
    return env(name, default="1" if default else "0").lower() not in {"0", "false", "no", "off"}


def log(msg: str) -> None:
    print(msg, flush=True)


MIN_WORDS = env_int("MIN_WORDS", default=1800)
LOOKBACK_DAYS = env_int("LOOKBACK_DAYS", default=14)
POST_SCOPE = env("POST_SCOPE", default="all").lower()
# One GraphQL request returns the whole window, so the limit exists to bound a
# pathological day rather than to ration requests. The old default of 30 was
# below LessWrong's actual posting rate, which silently truncated busy days and
# made the reported "Lookback: 14 days" false in every report ever produced.
POST_LIMIT = env_int("POST_LIMIT", default=100)
PAGE_SIZE = env_int("LW_PAGE_SIZE", default=50)
USER_AGENT = env(
    "LESSWRONG_USER_AGENT",
    default="Universal-Web-Monitoring-Agent/1.0 "
    "(+https://github.com/Erikiss/Universal-Web-Monitoring-Agent)",
)
REQUEST_TIMEOUT = env_int("LW_REQUEST_TIMEOUT", "REQUEST_TIMEOUT", default=60)
# Two independent budgets, as in arxiv_top_papers.py: transport/server errors
# are usually permanent, rate limiting usually passes.
MAX_RETRIES = env_int("LW_MAX_RETRIES", "MAX_RETRIES", default=5)
MAX_429_RETRIES = env_int("LW_MAX_429_RETRIES", default=6)
MAX_BACKOFF_SECONDS = env_int("LW_MAX_BACKOFF_SECONDS", "MAX_BACKOFF_SECONDS", default=60)
# Retry-After is honoured but never unbounded: a large value would otherwise
# park the job for hours.
RETRY_AFTER_CAP = env_int("LW_RETRY_AFTER_CAP", default=300)
# Consecutive budget-less 429/403 responses after which we stop retrying and
# fall through to the next transport. See looks_like_standing_block().
BLOCK_STRIKES = env_int("LW_BLOCK_STRIKES", default=2)
# robots.txt asks for Crawl-Delay: 3.
CRAWL_DELAY = float(env_int("LW_CRAWL_DELAY", default=3))
# Total wall clock this run may spend waiting out retries, across all
# transports. Must stay comfortably below the workflow's timeout-minutes.
TIME_BUDGET_SECONDS = env_int("LW_TIME_BUDGET_MINUTES", default=10) * 60
TRANSPORT = env("LW_TRANSPORT", default="auto").lower()
STRICT = env_flag("LW_STRICT", default=True)

TOPIC_RE = re.compile(
    r"\b("
    r"machine learning|deep learning|neural network|neural networks|"
    r"large language model|language model|llm|llms|gpt|claude|"
    r"nlp|natural language|transformer|embedding|embeddings|"
    r"reinforcement learning|rl|mechanistic interpretability|"
    r"sparse autoencoder|sae|ai alignment|alignment|"
    r"classifier|gradient|loss function|scaling law"
    r")\b",
    re.IGNORECASE,
)
VIS_RE = re.compile(
    r"\b("
    r"diagram|chart|plot|graph|matrix|correlation|heatmap|"
    r"figure|visualization|visualisation|scatter|histogram|"
    r"table|axis|axes|simulation"
    r")\b",
    re.IGNORECASE,
)

# posts(input: {terms: ...}) is @deprecated("Use the selector field instead").
# The selector form also exposes offset, which the terms form did not.
QUERY = """
query RecentPosts($limit: Int!, $offset: Int!, $after: String) {
  posts(
    selector: { new: { after: $after } }
    limit: $limit
    offset: $offset
    enableTotal: true
  ) {
    totalCount
    results {
      _id
      title
      slug
      postedAt
      frontpageDate
      baseScore
      wordCount
      contents {
        html
      }
      user {
        displayName
        username
      }
    }
  }
}
"""

# The `rss` view is "newest, everything"; `frontpage-rss` and `community-rss`
# are the frontpage/personal splits (packages/lesswrong/lib/collections/posts/views.ts).
FEED_VIEWS_BY_SCOPE = {
    "all": ("rss", "frontpage-rss"),
    "frontpage": ("frontpage-rss",),
    "personal": ("community-rss",),
}
FRONTPAGE_FEED_VIEWS = {"frontpage-rss", "curated-rss"}
DC_NS = "{http://purl.org/dc/elements/1.1/}"
# server/rss.ts appends a "Discuss" permalink to every description.
DISCUSS_SUFFIX_RE = re.compile(
    r"(?:<br\s*/?>\s*)*<a\s+href=\"[^\"]*#comments\">Discuss</a>\s*\Z",
    re.IGNORECASE,
)
MATCHES_LINE_RE = re.compile(r"^Matches:\s*(\d+)\s*$", re.MULTILINE)

# Headers worth printing when a request is refused. Without these the previous
# implementation left no evidence at all: four days of failures were only
# diagnosable by reverse-engineering the job's wall-clock time.
DIAGNOSTIC_HEADERS = (
    "retry-after",
    "server",
    "via",
    "age",
    "content-type",
    "x-vercel-id",
    "x-vercel-cache",
    "x-deny-reason",
    "x-robots-tag",
    "cf-ray",
    "cf-mitigated",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
)
# A rate limiter tells you about your budget; an edge deny rule does not.
BUDGET_HEADERS = (
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
)
RATE_LIMIT_ERROR_CODES = {"RATE_LIMITED", "TOO_MANY_REQUESTS", "RATE_LIMIT_EXCEEDED"}


class FetchError(RuntimeError):
    """No posts could be retrieved over this transport."""


class BlockedError(FetchError):
    """The request was refused in a way that retrying cannot clear."""


class RateLimitExceededError(FetchError):
    """The rate-limit retry budget was exhausted."""


class TimeBudgetExceededError(FetchError):
    """The run spent its whole retry allowance without getting an answer."""


def load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()

    raw = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{SEEN_FILE} must contain a JSON array of post IDs")
    return {str(post_id) for post_id in raw}


def save_seen(seen: set[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_last_request = 0.0


def throttle() -> None:
    global _last_request
    wait = _last_request + CRAWL_DELAY - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


_deadline: float | None = None


def start_deadline() -> None:
    """Bound total time spent retrying, so the job cannot outrun its own timeout.

    Per-wait caps are not enough: MAX_429_RETRIES waits of RETRY_AFTER_CAP each,
    across two transports, can exceed the workflow's timeout-minutes. GitHub
    enforces that timeout by *cancelling* the job, and a cancelled job skips the
    `if: failure()` alert — turning a loud failure back into a silent one.
    """
    global _deadline
    _deadline = time.monotonic() + TIME_BUDGET_SECONDS if TIME_BUDGET_SECONDS > 0 else None


def budget_remaining() -> float:
    if _deadline is None:
        return float("inf")
    return _deadline - time.monotonic()


def sleep_for(seconds: float, reason: str) -> None:
    # Jitter keeps a retrying fleet from re-synchronising on the same second.
    delay = max(0.0, seconds) * random.uniform(0.8, 1.2)
    remaining = budget_remaining()
    if delay > remaining:
        raise TimeBudgetExceededError(
            f"the {TIME_BUDGET_SECONDS / 60:.0f}-minute retry budget cannot absorb another "
            f"{delay:.0f}s wait ({reason}); {max(0.0, remaining):.0f}s left"
        )
    log(f"  backing off {delay:.1f}s ({reason})")
    time.sleep(delay)


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After as delta-seconds or as an HTTP-date. None if unparseable."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def describe(response: requests.Response, label: str, attempt: int) -> str:
    headers = {
        name: response.headers[name]
        for name in DIAGNOSTIC_HEADERS
        if name in response.headers
    }
    body = (response.text or "").strip().replace("\n", " ")[:300]
    return (
        f"{label}: HTTP {response.status_code} in "
        f"{response.elapsed.total_seconds():.2f}s (attempt {attempt}) "
        f"headers={headers} body={body!r}"
    )


def looks_like_standing_block(response: requests.Response) -> bool:
    """True when a refusal carries none of the marks of an application limiter.

    An application rate limiter reports the budget it is enforcing and answers
    in the content type it normally speaks. A refusal with no budget headers,
    a non-JSON body and near-zero latency is an edge/WAF deny rule instead —
    the request never reached the application, so waiting changes nothing.
    """
    if any(name in response.headers for name in BUDGET_HEADERS):
        return False
    if "json" in response.headers.get("content-type", "").lower():
        return False
    return response.elapsed.total_seconds() < 0.5


def request(session: requests.Session, method: str, url: str, *, label: str, **kwargs: Any) -> requests.Response:
    """Perform one request with separate error and rate-limit retry budgets.

    Raises BlockedError for refusals that retrying cannot clear, and
    RateLimitExceededError when the rate-limit budget runs out.
    """
    errors = 0
    throttles = 0
    strikes = 0
    last_error: str | None = None

    while errors < MAX_RETRIES and throttles < MAX_429_RETRIES:
        attempt = errors + throttles + 1
        throttle()
        try:
            response = session.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            errors += 1
            # Anything that is not a budget-less 429 breaks the run of strikes,
            # so the counter keeps meaning "consecutive".
            strikes = 0
            last_error = f"{type(exc).__name__}: {exc}"
            log(f"{label}: request failed ({errors}/{MAX_RETRIES}): {exc}")
            if errors >= MAX_RETRIES:
                break
            sleep_for(min(MAX_BACKOFF_SECONDS, 2**errors * 2), "network error")
            continue

        status = response.status_code

        if status in (401, 403):
            log(describe(response, label, attempt))
            raise BlockedError(
                f"{label}: HTTP {status} — access refused outright. This is not a "
                "quota; retrying the same request cannot clear it."
            )

        if status == 429:
            log(describe(response, label, attempt))
            if looks_like_standing_block(response):
                strikes += 1
                if strikes >= BLOCK_STRIKES:
                    raise BlockedError(
                        f"{label}: HTTP 429 on {strikes} consecutive attempts with no "
                        "rate-limit budget headers, a non-JSON body and near-zero "
                        "latency. That is an edge deny rule rather than a quota, so "
                        "the request never reached LessWrong's application and "
                        "waiting longer will not help."
                    )
            else:
                strikes = 0
            throttles += 1
            last_error = f"HTTP 429 ({throttles} rate-limit waits)"
            if throttles >= MAX_429_RETRIES:
                break
            header_wait = parse_retry_after(response.headers.get("Retry-After"))
            backoff = min(MAX_BACKOFF_SECONDS, 5 * 2 ** (throttles - 1))
            # Retry-After may legitimately exceed MAX_BACKOFF_SECONDS, but never
            # RETRY_AFTER_CAP.
            sleep_for(min(RETRY_AFTER_CAP, max(backoff, header_wait or 0.0)), "HTTP 429")
            continue

        if status >= 500:
            log(describe(response, label, attempt))
            errors += 1
            # A 5xx is positive evidence the request reached an origin, which is
            # the opposite of the edge-deny-rule conclusion strikes encodes.
            strikes = 0
            last_error = f"HTTP {status}"
            if errors >= MAX_RETRIES:
                break
            sleep_for(min(MAX_BACKOFF_SECONDS, 2**errors * 5), f"HTTP {status}")
            continue

        if status >= 400:
            log(describe(response, label, attempt))
            raise FetchError(f"{label}: HTTP {status} — request rejected, not retrying.")

        return response

    summary = (
        f"{label}: giving up after {errors} error(s) and {throttles} rate-limit "
        f"wait(s). Last failure: {last_error}"
    )
    # Classify by which budget actually ran out. Testing "did we ever see a 429"
    # would relabel a run of network errors as rate limiting, and callers treat
    # the two differently.
    if throttles >= MAX_429_RETRIES:
        raise RateLimitExceededError(summary)
    raise FetchError(summary)


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    return session


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

def normalize_graphql_post(post: dict[str, Any]) -> dict[str, Any]:
    user = post.get("user") or {}
    slug = post.get("slug") or ""
    return {
        "id": str(post["_id"]),
        "title": post.get("title") or "Untitled",
        "url": f"{SITE}/posts/{post['_id']}/{slug}".rstrip("/"),
        "postedAt": post.get("postedAt"),
        "author": user.get("displayName") or user.get("username") or "unknown",
        "section": "Frontpage" if post.get("frontpageDate") else "Personal/All",
        "sectionConfirmed": True,
        "html": ((post.get("contents") or {}).get("html") or "").strip(),
        "baseScore": post.get("baseScore"),
        "wordCount": post.get("wordCount"),
    }


def fetch_via_graphql(session: requests.Session, after: str, notes: list[str]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    offset = 0
    total: int | None = None
    # Backstop against a server that dribbles out one row per request: the
    # loop would otherwise issue POST_LIMIT requests before stopping.
    pages = 0
    max_pages = -(-POST_LIMIT // max(1, PAGE_SIZE)) + 3

    while len(collected) < POST_LIMIT and pages < max_pages:
        pages += 1
        page_size = min(PAGE_SIZE, POST_LIMIT - len(collected))
        response = request(
            session,
            "POST",
            ENDPOINT,
            label=f"graphql offset={offset}",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={"query": QUERY, "variables": {"limit": page_size, "offset": offset, "after": after}},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            body = (response.text or "").strip().replace("\n", " ")[:300]
            raise FetchError(
                f"graphql offset={offset}: HTTP {response.status_code} but the body is not "
                f"JSON ({exc}). First 300 chars: {body!r}"
            ) from exc

        errors = payload.get("errors") or []
        data = (payload.get("data") or {}).get("posts") or {}
        results = data.get("results") or []
        if errors:
            codes = {
                str((error.get("extensions") or {}).get("code", "")).upper()
                for error in errors
                if isinstance(error, dict)
            }
            messages = "; ".join(str(error.get("message", error)) for error in errors if isinstance(error, dict))
            if codes & RATE_LIMIT_ERROR_CODES:
                raise RateLimitExceededError(f"graphql: rate limited by the API: {messages}")
            if not results:
                raise FetchError(f"graphql: query failed (codes={sorted(codes)}): {messages}")
            # Partial data is still worth reporting, but never silently.
            log(f"graphql offset={offset}: partial response with errors: {messages}")
            notes.append(f"GraphQL returned partial data with errors: {messages}")

        if total is None:
            total = data.get("totalCount")

        added = 0
        for post in results:
            post_id = str(post.get("_id") or "")
            if not post_id or post_id in seen_ids:
                continue
            seen_ids.add(post_id)
            collected.append(normalize_graphql_post(post))
            added += 1

        if not results:
            break
        if not added:
            # A whole page of posts we already have means offset is not
            # advancing server-side; stop rather than loop forever.
            log(f"graphql offset={offset}: page contained no new posts, stopping pagination")
            break
        # Advance by what the server actually returned, not by what was asked
        # for: a server-side clamp below PAGE_SIZE is not end-of-window.
        offset += len(results)
        if total is not None and offset >= total:
            break
        if len(results) < page_size and total is None:
            # Without totalCount a short page is the only end-of-window signal.
            break

    log(f"graphql: fetched {len(collected)} post(s)"
        + (f" of {total} in the window" if total is not None else ""))
    if total is not None and total > len(collected):
        if len(collected) >= POST_LIMIT:
            reason = f"POST_LIMIT={POST_LIMIT} truncated the window"
        else:
            # Stopping short of POST_LIMIT means the server stopped feeding us,
            # so blaming POST_LIMIT would send the operator to the wrong knob.
            reason = "the server stopped returning results before POST_LIMIT was reached"
        notes.append(
            f"{reason}: {total} posts matched the {LOOKBACK_DAYS}-day lookback but "
            f"only {len(collected)} were fetched."
        )
        log(f"WARNING: {notes[-1]}")
    return collected


def parse_feed(xml_text: str, view: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    posts: list[dict[str, Any]] = []
    for item in root.iter("item"):
        guid = (item.findtext("guid") or "").strip()
        if not guid:
            continue
        html = (item.findtext("description") or "").strip()
        html = DISCUSS_SUFFIX_RE.sub("", html).strip()
        author = (item.findtext(f"{DC_NS}creator") or item.findtext("author") or "").strip()
        posted_at = (item.findtext("pubDate") or "").strip()
        try:
            # server/rss.ts dates the item by max(view date, karma-threshold
            # date), which can run slightly later than postedAt.
            parsed = parsedate_to_datetime(posted_at)
            # RFC 822 "-0000" parses to a naive datetime, and naive
            # .astimezone() would assume the runner's local zone.
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            posted_iso = parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            posted_iso = posted_at or None
        posts.append(
            {
                "id": guid,
                "title": (item.findtext("title") or "Untitled").strip(),
                "url": (item.findtext("link") or f"{SITE}/posts/{guid}").strip(),
                "postedAt": posted_iso,
                "author": author or "unknown",
                "section": "Frontpage" if view in FRONTPAGE_FEED_VIEWS else "Personal/All",
                "sectionConfirmed": view in FRONTPAGE_FEED_VIEWS,
                "html": html,
                "baseScore": None,
                "wordCount": None,
            }
        )
    return posts


def fetch_via_feed(session: requests.Session, after: str, notes: list[str]) -> list[dict[str, Any]]:
    views = env("LW_FEED_VIEWS", default=",".join(FEED_VIEWS_BY_SCOPE[POST_SCOPE]))
    view_list = [view.strip() for view in views.split(",") if view.strip()]
    by_id: dict[str, dict[str, Any]] = {}
    failures: list[str] = []

    for view in view_list:
        try:
            response = request(
                session,
                "GET",
                FEED_ENDPOINT,
                label=f"feed view={view}",
                # Deliberately no `after` param. A query string is part of the
                # edge cache key, and a value that changes daily would guarantee
                # a MISS on every first request — going to the origin, which is
                # the path being refused. The feed only returns 10 recent items
                # anyway, so the window is enforced client-side instead.
                params={"view": view},
                headers={"Accept": "application/rss+xml, application/xml;q=0.9"},
            )
            posts = parse_feed(response.text, view)
        except ET.ParseError as exc:
            failures.append(f"{view}: malformed XML ({exc})")
            log(f"feed view={view}: malformed XML: {exc}")
            continue
        except (BlockedError, RateLimitExceededError):
            # Whatever refused one view will refuse the rest; do not spend a
            # second full retry budget to confirm it.
            raise
        except FetchError as exc:
            failures.append(f"{view}: {exc}")
            log(f"feed view={view}: {exc}")
            continue

        log(f"feed view={view}: {len(posts)} item(s)")
        for post in posts:
            existing = by_id.get(post["id"])
            if existing is None:
                by_id[post["id"]] = post
            elif post["sectionConfirmed"] and not existing["sectionConfirmed"]:
                # frontpage-rss membership is the only positive evidence of a
                # post's section on this transport; prefer it.
                existing["section"] = post["section"]
                existing["sectionConfirmed"] = True

    if not by_id:
        raise FetchError("feed: no items from any view (" + "; ".join(failures) + ")")
    if failures:
        notes.append("Some RSS views failed: " + "; ".join(failures))

    notes.append(
        "Fetched over the RSS feed, which the server caps at 10 items per view: "
        "coverage is partial, `baseScore` is unavailable, and a Frontpage post "
        "outside the 10 newest frontpage items is reported as Personal/All."
    )
    return sorted(by_id.values(), key=lambda post: post["postedAt"] or "", reverse=True)[:POST_LIMIT]


def parse_timestamp(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def within_window(posts: list[dict[str, Any]], after: str) -> list[dict[str, Any]]:
    """Drop posts older than the lookback window.

    Applied to every transport so the report's "Lookback: N days" is true
    regardless of whether the server honoured the cutoff — the feed transport
    is not sent one at all, to keep its URL cacheable.
    """
    cutoff = parse_timestamp(after)
    if cutoff is None:
        return posts
    kept = []
    for post in posts:
        posted = parse_timestamp(post.get("postedAt"))
        # An unparseable date is kept: dropping a post over a formatting quirk
        # loses more than an occasional stale entry.
        if posted is None or posted >= cutoff:
            kept.append(post)
    dropped = len(posts) - len(kept)
    if dropped:
        log(f"dropped {dropped} post(s) older than {after}")
    return kept


def fetch_posts(session: requests.Session, after: str, notes: list[str]) -> tuple[list[dict[str, Any]], str]:
    transports: list[tuple[str, Any]] = [("graphql", fetch_via_graphql), ("feed", fetch_via_feed)]
    if TRANSPORT == "graphql":
        transports = transports[:1]
    elif TRANSPORT == "feed":
        transports = transports[1:]
    elif TRANSPORT != "auto":
        raise ValueError("LW_TRANSPORT must be one of: auto, graphql, feed")

    failures: list[str] = []
    for name, fetch in transports:
        log(f"--- transport: {name}")
        try:
            posts = within_window(fetch(session, after, notes), after)
        except TimeBudgetExceededError as exc:
            # The allowance is shared across transports; trying the next one
            # would only run the job into its own timeout.
            log(f"transport {name} failed: {exc}")
            failures.append(f"{name}: {exc}")
            break
        except FetchError as exc:
            log(f"transport {name} failed: {exc}")
            failures.append(f"{name}: {exc}")
            continue
        if posts:
            return posts, name
        # LessWrong publishes on the order of 20 posts a day, so an empty
        # window is a broken transport rather than a quiet fortnight.
        failures.append(f"{name}: returned no posts at all for the last {LOOKBACK_DAYS} days")
        log(f"transport {name} returned no posts")

    raise FetchError("all transports failed — " + " | ".join(failures))


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def html_text(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)


def estimate_words(post: dict[str, Any]) -> int:
    if post.get("wordCount"):
        return int(post["wordCount"])
    return len(re.findall(r"\b\w+\b", html_text(post.get("html", ""))))


def visualization_score(html: str) -> tuple[int, list[str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    score = 0
    evidence: list[str] = []

    for tag in soup.find_all(["img", "figure", "svg", "canvas", "iframe", "table"]):
        previous_text = " ".join(
            sibling.get_text(" ", strip=True)
            for sibling in tag.find_previous_siblings(limit=2)
            if hasattr(sibling, "get_text")
        )
        next_text = " ".join(
            sibling.get_text(" ", strip=True)
            for sibling in tag.find_next_siblings(limit=2)
            if hasattr(sibling, "get_text")
        )
        text = " ".join(
            [
                tag.name,
                tag.get("alt", ""),
                tag.get("title", ""),
                tag.get("aria-label", ""),
                tag.get("src", ""),
                tag.get_text(" ", strip=True),
                previous_text,
                next_text,
            ]
        )

        score += 2 if tag.name in {"svg", "canvas", "table"} else 1
        if VIS_RE.search(text):
            score += 2
        evidence.append(text[:180])

    return score, evidence[:5]


def topic_score(title: str, text: str) -> int:
    haystack = f"{title}\n{text[:8000]}"
    return len(TOPIC_RE.findall(haystack))


def include_post(post: dict[str, Any]) -> bool:
    if POST_SCOPE == "all":
        return True
    is_frontpage = post["section"] == "Frontpage"
    if POST_SCOPE == "frontpage":
        return is_frontpage
    if POST_SCOPE == "personal":
        return not is_frontpage
    raise ValueError("POST_SCOPE must be one of: all, frontpage, personal")


def classify(post: dict[str, Any]) -> dict[str, Any] | None:
    if not include_post(post):
        return None

    html = post["html"]
    words = estimate_words(post)
    if words < MIN_WORDS:
        return None

    vis_score, vis_evidence = visualization_score(html)
    if vis_score < 2:
        return None

    t_score = topic_score(post["title"], html_text(html))
    if t_score < 1:
        return None

    return {
        "id": post["id"],
        "title": post["title"],
        "url": post["url"],
        "postedAt": post["postedAt"],
        "author": post["author"],
        "section": post["section"] if post["sectionConfirmed"] else f"{post['section']} (unconfirmed)",
        "wordCount": words,
        "baseScore": post["baseScore"],
        "visualizationScore": vis_score,
        "topicScore": t_score,
        "visualEvidence": vis_evidence,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def render_report(
    matches: list[dict[str, Any]],
    today: str,
    transport: str,
    fetched: int,
    oldest: str | None,
    notes: list[str] | None = None,
    failure: str | None = None,
) -> str:
    lines = [f"# LessWrong ML/NLP visual long-post filter — {today}", ""]
    if failure:
        # Stated before anything else, so a failed day cannot be mistaken for a
        # quiet one by a reader skimming for `Matches:`. The full diagnostic
        # goes in the notes below rather than here, to keep this scannable.
        lines.append(
            "**STATUS: FAILED — could not fetch posts.** No posts were examined, so "
            "any `Matches:` count below covers only what earlier runs today already "
            "found — it does not mean nothing was published. See the notes for the "
            "full diagnostic."
        )
        lines.append("")
    lines.append(f"Lookback: {LOOKBACK_DAYS} days")
    lines.append(f"Minimum words: {MIN_WORDS}")
    lines.append(f"Post limit: {POST_LIMIT}")
    lines.append(f"Post scope: {POST_SCOPE}")
    lines.append(f"Transport: {transport}")
    lines.append(f"Posts fetched: {fetched}" + (f" (oldest: {oldest})" if oldest else ""))
    if notes:
        lines.append("")
        lines.append("Notes:")
        for note in notes:
            lines.append(f"- {note}")
    lines.append("")
    lines.append(f"Matches: {len(matches)}")
    lines.append("")

    for match in sorted(
        matches,
        key=lambda item: (item["topicScore"], item["visualizationScore"], item["wordCount"]),
        reverse=True,
    ):
        lines.append(f"## [{match['title']}]({match['url']})")
        lines.append(f"- Author: {match['author']}")
        lines.append(f"- Section: {match['section']}")
        lines.append(f"- Posted: {match['postedAt']}")
        lines.append(f"- Words: {match['wordCount']}")
        lines.append(f"- Karma/baseScore: {match['baseScore'] if match['baseScore'] is not None else 'n/a'}")
        lines.append(f"- Topic score: {match['topicScore']}")
        lines.append(f"- Visualization score: {match['visualizationScore']}")
        if match["visualEvidence"]:
            lines.append("- Visualization evidence:")
            for snippet in match["visualEvidence"]:
                lines.append(f"  - {snippet}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def existing_match_count(path: Path) -> int:
    if not path.exists():
        return 0
    found = MATCHES_LINE_RE.search(path.read_text(encoding="utf-8"))
    return int(found.group(1)) if found else 0


def match_state_path(today: str) -> Path:
    # Resolved at call time so it always tracks OUT_DIR.
    return OUT_DIR / MATCH_STATE_DIRNAME / f"{today}.json"


def load_day_matches(today: str) -> list[dict[str, Any]]:
    """This day's already-reported matches, so a rerun can render their union.

    Reports are keyed by UTC date, but a rerun only classifies posts absent from
    ``seen.json`` — so its match set is disjoint from the earlier run's, and
    rewriting the file from this run's matches alone would delete them. They
    could never be re-derived, because their ids are already in ``seen.json``.
    """
    path = match_state_path(today)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        log(f"WARNING: could not read {path} ({exc}); treating the day as empty")
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("id")]


def save_day_matches(today: str, matches: list[dict[str, Any]]) -> None:
    path = match_state_path(today)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(matches, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def merge_matches(previous: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {match["id"]: match for match in previous}
    merged.update({match["id"]: match for match in new})
    return list(merged.values())


def write_report(path: Path, content: str, match_count: int) -> bool:
    """Write the report unless doing so would drop matches it already lists.

    With the day's match index this cannot normally trigger: the rendered set is
    the union of everything reported today. It remains as a backstop for the one
    case the index cannot cover — a report written before the index existed, or
    one whose index file was lost.
    """
    previous = existing_match_count(path)
    if match_count < previous:
        log(
            f"Refusing to overwrite {path}: it already reports {previous} match(es) "
            f"and this run would write {match_count}. Its match index is missing, so "
            "the earlier entries cannot be merged. Delete the file to force a rewrite."
        )
        return False
    path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    if POST_SCOPE not in FEED_VIEWS_BY_SCOPE:
        log(f"ERROR: POST_SCOPE must be one of: {', '.join(FEED_VIEWS_BY_SCOPE)}")
        return 2

    OUT_DIR.mkdir(exist_ok=True)
    start_deadline()
    after = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    notes: list[str] = []
    session = make_session()

    log(
        f"Fetching LessWrong posts since {after} "
        f"(scope={POST_SCOPE}, limit={POST_LIMIT}, transport={TRANSPORT}, strict={STRICT}, "
        f"retry budget={TIME_BUDGET_SECONDS // 60}min)"
    )

    failure: str | None = None
    try:
        posts, transport = fetch_posts(session, after, notes)
    except FetchError as exc:
        # Still write the report. The annotated report is the durable record of
        # an outage — the Actions log expires, reports/ is in git — and it is
        # what makes a run of bad days visible after the fact. What it must not
        # do is stand in for a notification, so the run also goes red below.
        log(f"ERROR: {exc}")
        failure = str(exc)
        posts, transport = [], "none"
        notes.append(f"Could not fetch posts: {exc}")

    seen = load_seen()
    matches: list[dict[str, Any]] = []
    skipped_empty = 0

    for post in posts:
        if post["id"] in seen:
            continue
        if not post["html"]:
            # Do not burn the id: an empty body is usually a transport quirk,
            # and marking it seen would drop the post permanently.
            skipped_empty += 1
            continue
        result = classify(post)
        if result:
            matches.append(result)
        seen.add(post["id"])

    if skipped_empty:
        notes.append(f"{skipped_empty} post(s) arrived without body content and were left unprocessed.")
        log(f"WARNING: {notes[-1]}")

    oldest = min((post["postedAt"] for post in posts if post["postedAt"]), default=None)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = OUT_DIR / f"{today}.md"
    # Render everything reported today, not just this run's share, so a second
    # run of the day adds to the report instead of replacing it.
    all_today = merge_matches(load_day_matches(today), matches)
    content = render_report(all_today, today, transport, len(posts), oldest, notes, failure)

    if not write_report(report_path, content, len(all_today)):
        # The matches this run found reached no report, so their ids must not be
        # recorded — seen.json is the only suppression list, and persisting them
        # would hide those posts forever.
        save_seen(seen - {match["id"] for match in matches})
        log(f"ERROR: {len(matches)} match(es) were discarded rather than written.")
        return 1

    save_day_matches(today, all_today)
    save_seen(seen)
    log(
        f"Wrote {report_path} with {len(all_today)} match(es) "
        f"({len(matches)} from this run, transport={transport})."
    )
    if failure and STRICT:
        log(
            "Exiting non-zero: the report records the outage, but a committed "
            "note is not a notification. Set LW_STRICT=0 for a green run."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
