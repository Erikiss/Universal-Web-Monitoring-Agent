"""Conservative DFT video-stream announcement watcher; no login or block bypass.

Only public organizer/partner pages and bounded same-host event links are read.
A dated video-stream announcement is evidence, not proof that a player is live.
Delivery and persistence are separate workflow steps: never mark an unsent alert.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

PACIFIC = ZoneInfo("America/Los_Angeles")
BERLIN = ZoneInfo("Europe/Berlin")
UA = "DFTLivestreamWatch/1.0 (+https://github.com/Erikiss/Universal-Web-Monitoring-Agent)"
SEEDS = (
    "https://www.dftsf.com/",
    "https://civicjoyfund.org/projects/dft",
    "https://downtownsf.org/do/downtown-first-thursdays-1",
    "https://111minnagallery.com/",
)
EVENT = re.compile(r"downtown\s+first\s+thursdays?|\bdft\b|dftsf", re.I)
LIVE = re.compile(r"live[\s-]*stream|watch\s+(?:us\s+)?live|streaming\s+live|live\s+(?:on|via)\s+(?:youtube|instagram|twitch|tiktok|facebook)|live\s+video", re.I)
PAST = re.compile(r"\b(?:replay|recap|highlights|recorded|was live|streamed live|last month|yesterday)\b", re.I)
MONTHS = {m.lower(): i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
MONTH_DATE = re.compile(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b", re.I)


def event_day(now: datetime) -> date | None:
    if now.tzinfo is None:
        raise ValueError("An aware timestamp is required")
    local = now.astimezone(PACIFIC)
    return local.date() if local.weekday() == 3 and local.day <= 7 else None


def next_event(now: datetime) -> date:
    local = now.astimezone(PACIFIC).date()
    first = local.replace(day=1)
    candidate = first + timedelta(days=(3 - first.weekday()) % 7)
    if candidate < local:
        first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        candidate = first + timedelta(days=(3 - first.weekday()) % 7)
    return candidate


def dates_in(text: str, year: int) -> set[date]:
    found = set()
    for value in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", text):
        try:
            found.add(date.fromisoformat(value))
        except ValueError:
            pass
    for month, day, explicit_year in MONTH_DATE.findall(text):
        try:
            found.add(date(int(explicit_year or year), MONTHS[month[:3].lower()], int(day)))
        except ValueError:
            pass
    return found


def canonical(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return ""
    host = p.hostname.lower().removeprefix("www.")
    query = parse_qs(p.query)
    if host in ("youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com"):
        parts = p.path.strip("/").split("/")
        video_id = query.get("v", [""])[0]
        if host == "youtu.be":
            video_id = parts[0]
        elif parts[0] in ("live", "embed") and len(parts) > 1:
            video_id = parts[1]
        if video_id:
            return "https://www.youtube.com/watch?" + urlencode({"v": video_id})
    clean_query = {k: v for k, v in query.items() if not k.startswith("utm_") and k not in ("fbclid", "gclid", "si")}
    return urlunsplit(("https", p.netloc.lower(), p.path.rstrip("/"), urlencode(clean_query, doseq=True), ""))


def video_link(url: str) -> bool:
    p = urlsplit(url)
    host = (p.hostname or "").removeprefix("www.")
    path = p.path.lower()
    if host in ("youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com"):
        return bool(parse_qs(p.query).get("v")) or "/live" in path or "/embed/" in path or host == "youtu.be"
    if host in ("twitch.tv", "m.twitch.tv"):
        return bool(path.strip("/")) and not any(x in path for x in ("/videos/", "/clip/", "/directory"))
    if host in ("instagram.com", "tiktok.com", "facebook.com", "fb.watch"):
        return "/live" in path or (host == "facebook.com" and "/videos/" in path)
    # An explicit watch/stream URL on another site can also be an official player.
    return bool(re.search(r"/(?:live|livestream|watch|stream)(?:/|$|-)", path)) and "kalw" not in host


def extract(html: str, source: str, day: date) -> tuple[list[dict], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script, style, nav, footer, header"):
        node.decompose()
    text = soup.get_text(" ", strip=True)
    if len(text) < 150 or any(x in text.lower() for x in ("verify you are human", "checking your browser", "enable javascript and cookies to continue")):
        raise RuntimeError("Unreadable/blocked page, not an empty result")
    if not EVENT.search(text):
        return [], []
    heading = " ".join(n.get_text(" ", strip=True) for n in soup.select("h1, h2, h3"))[:1200] + " " + text[:500]
    header_dates = dates_in(heading, day.year)
    event_scoped = bool(EVENT.search(heading) or EVENT.search(source.replace("-", " ")))
    hits, children = {}, []
    for node in soup.select("a[href], iframe[src]"):
        raw_url = urljoin(source, node.get("href") or node.get("src") or "")
        url = canonical(raw_url)
        if not url:
            continue
        label = node.get_text(" ", strip=True) + " " + node.get("title", "")
        parent = node.parent
        context = label
        for _ in range(3):
            if parent is None or parent.name in ("body", "html"):
                break
            candidate = parent.get_text(" ", strip=True)
            if len(candidate) > 1800:
                break
            context = candidate + " " + label
            if len(candidate) > len(label) + 60:
                break
            parent = parent.parent
        if urlsplit(url).hostname == urlsplit(source).hostname and EVENT.search(label + " " + urlsplit(url).path):
            children.append(url)
        if not video_link(url) or not LIVE.search(context) or PAST.search(context):
            continue
        if not event_scoped and not EVENT.search(context):
            continue
        # Never interpret live music, a radio broadcast, a generic social icon,
        # or an old video as a newly announced video livestream.
        local_dates = dates_in(context, day.year)
        if local_dates:
            fresh = day in local_dates
        else:
            fresh = day in header_dates
        if not fresh:
            continue
        hits[url] = {"url": url, "source": source, "evidence": " ".join(context.split())[:1000], "event_date": day.isoformat(), "kind": "dated_video_stream_announcement"}
    return list(hits.values()), list(dict.fromkeys(children))


class PublicReader:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.robots: dict[str, RobotFileParser] = {}
        self.last: dict[str, float] = {}
        self.started = time.monotonic()

    def request(self, url: str, delay: float = 2) -> requests.Response:
        if time.monotonic() - self.started > 240:
            raise RuntimeError("Source-check time budget exceeded")
        host = urlsplit(url).netloc
        time.sleep(max(0, delay - (time.monotonic() - self.last.get(host, 0))))
        self.last[host] = time.monotonic()
        response = self.session.get(url, timeout=(8, 20), allow_redirects=False)
        # Refusals are reported, not evaded. Log diagnostic data, not credentials.
        print(f"HTTP {response.status_code} {url} ({len(response.content)} bytes; {response.elapsed.total_seconds():.2f}s)")
        if response.status_code >= 400:
            print("Response:", response.headers.get("content-type", ""), "retry-after=", response.headers.get("retry-after", ""), re.sub(r"\s+", " ", response.text[:180]))
        return response

    def read(self, url: str, hops: int = 0) -> str:
        if hops > 3:
            raise RuntimeError("Too many redirects")
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self.robots:
            robots_url = origin + "/robots.txt"
            response = self.request(robots_url)
            for _ in range(3):
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                target = urljoin(robots_url, response.headers.get("Location", ""))
                if urlsplit(target).scheme != "https" or (urlsplit(target).hostname or "").removeprefix("www.") != (p.hostname or "").removeprefix("www."):
                    raise RuntimeError("robots.txt redirect leaves the source host")
                robots_url = target
                response = self.request(robots_url)
            robot = RobotFileParser()
            if response.status_code in (404, 410):
                robot.parse(["User-agent: *", "Allow: /"])
            elif response.status_code == 200 and "<html" not in response.text[:300].lower():
                robot.parse(response.text.splitlines())
            else:
                raise RuntimeError(f"Cannot establish robots.txt policy: HTTP {response.status_code}")
            self.robots[origin] = robot
        robot = self.robots[origin]
        if not robot.can_fetch("DFTLivestreamWatch", url):
            raise RuntimeError("robots.txt disallows this URL; no bypass attempted")
        delay = max(2, robot.crawl_delay("DFTLivestreamWatch") or 0)
        if delay > 60:
            raise RuntimeError("Requested crawl delay exceeds this short-run budget")
        response = self.request(url, delay)
        if response.status_code in (301, 302, 303, 307, 308):
            target = urljoin(url, response.headers.get("Location", ""))
            allowed_hosts = {urlsplit(u).hostname.removeprefix("www.") for u in SEEDS}
            if urlsplit(target).scheme != "https" or (urlsplit(target).hostname or "").removeprefix("www.") not in allowed_hosts:
                raise RuntimeError("Redirect leaves the configured public source hosts")
            return self.read(target, hops + 1)
        response.raise_for_status()
        if "html" not in response.headers.get("content-type", "").lower():
            raise RuntimeError("Expected an HTML page")
        return response.text


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"schema": 1, "sent": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != 1 or not isinstance(value.get("sent"), dict) or any(not isinstance(v, dict) for v in value["sent"].values()):
        raise ValueError("Invalid DFT state; refusing to reset deduplication")
    return value


def pending_hits(hits: list[dict], state: dict, day: date) -> list[dict]:
    sent = state["sent"].get(day.isoformat(), {})
    return list({h["url"]: h for h in hits if h["url"] not in sent}.values())


def output(name: str, value: str | int) -> None:
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state", type=Path, default=Path("seen_dft_livestreams.json"))
    parser.add_argument("--pending-state", type=Path, required=True)
    parser.add_argument("--alert-file", type=Path, required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    day = event_day(now)
    if day is None and not args.dry_run:
        print("Outside the first Thursday in America/Los_Angeles; no requests or email.")
        output("alert_count", 0)
        output("error_count", 0)
        return
    day = day or next_event(now)
    state = load_state(args.state)
    reader = PublicReader()
    queue = list(SEEDS)
    visited, hits, errors = set(), [], []
    while queue and len(visited) < 12:
        source = queue.pop(0)
        key = canonical(source)
        if key in visited:
            continue
        visited.add(key)
        try:
            matches, children = extract(reader.read(source), source, day)
            hits.extend(matches)
            queue.extend(u for u in children[:3] if canonical(u) not in visited)
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            errors.append(f"{source}: {type(exc).__name__}: {exc}")
    new = pending_hits(hits, state, day)
    lines = [f"DFT livestream check: {day.isoformat()} (San Francisco)", f"Checked at: {now.astimezone(BERLIN).isoformat()} (Berlin)", f"Pages attempted: {len(visited)}; errors: {len(errors)}; new announcements: {len(new)}", "", "Scope: public organizer/partner pages and bounded related links. No complete social-media coverage; login-only/ephemeral streams can be missed.", "A dated announcement is not verification that its video player is currently live."]
    for hit in new:
        lines.extend(["", "Video-Livestream-Hinweis: " + hit["url"], "Quelle: " + hit["source"], "Beleg: " + hit["evidence"]])
    if errors:
        lines.extend(["", "Incomplete source checks:", *errors])
    report = "\n".join(lines) + "\n"
    print(report)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(report)
    args.alert_file.write_text(report, encoding="utf-8")
    if new:
        sent = state["sent"].setdefault(day.isoformat(), {})
        for hit in new:
            sent[hit["url"]] = {"source": hit["source"], "detected_at": now.isoformat()}
        state["sent"] = dict(sorted(state["sent"].items())[-12:])
        args.pending_state.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output("alert_count", len(new))
    output("error_count", len(errors))
    output("subject", f"DFT {day.isoformat()}: Video-Livestream-Hinweis")


if __name__ == "__main__":
    main()
