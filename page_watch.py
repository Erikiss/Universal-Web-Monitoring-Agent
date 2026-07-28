"""Watch a research publications page and detect new entries.

Unlike the arXiv-based lab filter, this watcher scrapes a concrete URL and
compares the set of publication links against a persisted baseline. It fails
loudly when it cannot extract any entries, so a broken scraper produces a red
workflow run instead of weeks of silently empty reports.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

OUT_DIR = Path("reports")
USER_AGENT = os.getenv(
    "WATCH_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
)
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "4"))

ARXIV_VERSION_RE = re.compile(r"(/abs/[^/]+?)v\d+$")
SLUG_CLEAN_RE = re.compile(r"[-_]+")


@dataclass(frozen=True)
class Site:
    id: str
    name: str
    url: str
    include: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    # Fewer extracted entries than this means the scraper degraded (page
    # redesign, bot block, client-only rendering) — fail instead of silently
    # treating a broken page as "no news". Override with WATCH_MIN_ENTRIES.
    min_entries: int = 5


SITES = {
    "anthropic-interpretability": Site(
        id="anthropic-interpretability",
        name="Anthropic Interpretability team",
        url="https://www.anthropic.com/research/team/interpretability",
        include=(
            r"^https://anthropic\.com/research/[^/]+$",
            r"^https://anthropic\.com/news/[^/]+$",
            r"^https://transformer-circuits\.pub/.+",
            r"^https://arxiv\.org/abs/.+",
        ),
        exclude=(
            r"^https://anthropic\.com/research/team$",
            r"^https://anthropic\.com/(research|news)$",
        ),
    ),
    "goodfire-research": Site(
        id="goodfire-research",
        name="Goodfire research",
        url="https://www.goodfire.ai/research",
        include=(
            r"^https://goodfire\.ai/(research|papers|blog)/.+",
            r"^https://arxiv\.org/abs/.+",
            r"^https://openreview\.net/forum.+",
            r"^https://(www\.)?biorxiv\.org/content/.+",
            r"^https://nature\.com/articles/.+",
        ),
        exclude=(),
    ),
}


def seen_file(site: Site) -> Path:
    return Path(f"seen_{site.id.replace('-', '_')}.json")


def load_seen(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object mapping URL -> metadata")
    return raw


def save_seen(path: Path, seen: dict[str, dict[str, str]]) -> None:
    path.write_text(
        json.dumps(dict(sorted(seen.items())), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def fetch_page(site: Site) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(site.url, headers=headers, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200 and response.text.strip():
                return response.text
            last_error = RuntimeError(f"HTTP {response.status_code} from {site.url}")
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(min(30, 3 * 2**attempt))
    raise SystemExit(f"ERROR: could not fetch {site.url}: {last_error}")


def normalize_url(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    path = re.sub(r"/index\.html?$", "", path)
    # Query strings are usually tracking noise, but OpenReview needs ?id=...
    query = parts.query if host == "openreview.net" else ""
    normalized = urlunsplit(("https", host, path, query, ""))
    return ARXIV_VERSION_RE.sub(r"\1", normalized)


def url_allowed(url: str, site: Site) -> bool:
    if any(re.search(p, url) for p in site.exclude):
        return False
    return any(re.search(p, url) for p in site.include)


def title_from_slug(url: str) -> str:
    segments = [s for s in urlsplit(url).path.split("/") if s]
    while segments and segments[-1].lower() in ("index.html", "index.htm", "index"):
        segments.pop()
    slug = re.sub(r"\.html?$", "", segments[-1]) if segments else ""
    return SLUG_CLEAN_RE.sub(" ", slug).strip().title() or url


def anchor_title(anchor) -> str:
    heading = anchor.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    if heading:
        text = heading.get_text(" ", strip=True)
        if text:
            return text
    text = anchor.get_text(" ", strip=True) or anchor.get("aria-label", "").strip()
    return text[:200]


def better_title(current: str, candidate: str, url: str) -> str:
    slug_title = title_from_slug(url)
    for title in (current, candidate):
        if title and title != slug_title and len(title) >= 8:
            return title
    return current or candidate or slug_title


def extract_entries(html: str, site: Site) -> dict[str, str]:
    entries: dict[str, str] = {}
    soup = BeautifulSoup(html, "html.parser")

    # Strategy 1: anchors in the rendered HTML. Navigation chrome is skipped
    # so promo links there cannot masquerade as publication entries; those
    # URLs are also blocked for the raw-text scan below.
    blocked: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        url = normalize_url(urljoin(site.url, anchor["href"]))
        if not url or not url_allowed(url, site):
            continue
        if anchor.find_parent(["nav", "header", "footer"]):
            blocked.add(url)
            continue
        candidate = anchor_title(anchor)
        entries[url] = better_title(entries.get(url, ""), candidate, url)

    # Strategy 2: URLs embedded in scripts/JSON (Next.js flight data, Framer
    # props, ...) that are not plain anchors yet. JSON escapes "/" as "\/";
    # HTML entities must be decoded so keys match the anchor-derived ones.
    raw = html.replace("\\/", "/")
    for match in re.finditer(r"https?://[^\s\"'<>\\)\]}]+", raw):
        candidate = html_lib.unescape(match.group(0)).rstrip(".,;:!?")
        url = normalize_url(candidate)
        if url and url_allowed(url, site) and url not in entries and url not in blocked:
            entries[url] = title_from_slug(url)
    # In flight payloads the JSON string is itself quoted, so the closing
    # quote may be escaped: {\"href\":\"/research/foo\"} — allow a backslash
    # before the terminating quote.
    for match in re.finditer(r"[\"'](/[A-Za-z0-9][A-Za-z0-9\-_/.]*)\\?[\"']", raw):
        url = normalize_url(urljoin(site.url, html_lib.unescape(match.group(1))))
        if url and url_allowed(url, site) and url not in entries and url not in blocked:
            entries[url] = title_from_slug(url)

    for url in entries:
        if not entries[url]:
            entries[url] = title_from_slug(url)
    return entries


def write_github_output(new_count: int, baseline: bool) -> None:
    gh_output = os.getenv("GITHUB_OUTPUT")
    if not gh_output:
        return
    with open(gh_output, "a", encoding="utf-8") as handle:
        handle.write(f"new_count={new_count}\n")
        handle.write(f"baseline={'true' if baseline else 'false'}\n")


def render_report(
    site: Site, today: str, new_entries: dict[str, str],
    seen: dict[str, dict[str, str]], baseline: bool,
) -> str:
    lines = [f"# {site.name} watch — {today}", ""]
    lines.append(f"Watched page: {site.url}")
    lines.append(f"Known entries: {len(seen)}")
    lines.append("")
    if baseline:
        lines.append(
            f"Baseline initialized with {len(new_entries)} entries. "
            "Future runs alert only on entries not in this baseline."
        )
        lines.append("")
    lines.append(f"New entries: {0 if baseline else len(new_entries)}")
    lines.append("")
    for url, title in sorted(new_entries.items(), key=lambda item: item[1].lower()):
        lines.append(f"- [{title}]({url})")
    return "\n".join(lines).rstrip() + "\n"


def render_alert(site: Site, new_entries: dict[str, str]) -> str:
    lines = [
        f"{site.name}: {len(new_entries)} new entr{'y' if len(new_entries) == 1 else 'ies'} "
        f"detected on {site.url}",
        "",
    ]
    for url, title in sorted(new_entries.items(), key=lambda item: item[1].lower()):
        lines.append(f"- {title}")
        lines.append(f"  {url}")
    lines.append("")
    lines.append("Sent by Universal-Web-Monitoring-Agent.")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True, choices=sorted(SITES))
    parser.add_argument(
        "--alert-file",
        help="Write the alert email body here when new entries are found",
    )
    args = parser.parse_args(argv)
    site = SITES[args.site]

    html = fetch_page(site)
    entries = extract_entries(html, site)
    min_entries = int(os.getenv("WATCH_MIN_ENTRIES") or site.min_entries)
    if len(entries) < min_entries:
        raise SystemExit(
            f"ERROR: extracted only {len(entries)} entries from {site.url} "
            f"(expected at least {min_entries}). The page structure changed or "
            "the fetch was blocked; refusing to treat a degraded page as 'no news'."
        )

    seen_path = seen_file(site)
    baseline = not seen_path.exists()
    seen = load_seen(seen_path)
    new_entries = {url: title for url, title in entries.items() if url not in seen}

    now = datetime.now(timezone.utc)
    for url, title in new_entries.items():
        seen[url] = {"title": title, "first_seen": now.isoformat(timespec="seconds")}
    save_seen(seen_path, seen)

    today = now.strftime("%Y-%m-%d")
    if new_entries or baseline:
        OUT_DIR.mkdir(exist_ok=True)
        report_path = OUT_DIR / f"{site.id.replace('-', '_')}_{today}.md"
        report = render_report(site, today, new_entries, seen, baseline)
        if report_path.exists():
            # A second hit on the same day appends instead of overwriting.
            report = (
                report_path.read_text(encoding="utf-8")
                + f"\n---\n\nLater run at {now.strftime('%H:%M')} UTC:\n\n"
                + report
            )
        report_path.write_text(report, encoding="utf-8")
        print(f"Wrote {report_path}")

    alert_count = 0 if baseline else len(new_entries)
    if alert_count and args.alert_file:
        Path(args.alert_file).write_text(render_alert(site, new_entries), encoding="utf-8")

    write_github_output(alert_count, baseline)
    print(
        f"{site.name}: {len(entries)} entries on page, {len(new_entries)} new, "
        f"baseline={baseline}."
    )


if __name__ == "__main__":
    main(sys.argv[1:])
