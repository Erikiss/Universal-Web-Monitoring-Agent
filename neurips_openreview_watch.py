"""Watch the public NeurIPS 2026 OpenReview page, without account credentials.

The caller commits state only after send_alert.py successfully hands the alert
email to SMTP. A failed source never replaces its last good snapshot with zero.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

VENUE = "NeurIPS.cc/2026/Conference"
PAGE_URL = f"https://openreview.net/group?id={VENUE}"
API_URL = "https://api2.openreview.net/notes"
STATE = Path("seen_neurips_2026_openreview.json")
REPORT = Path("reports/neurips_2026_openreview_latest.md")
RELATIVE_TIME = re.compile(
    r"\b(?:\d+|a|an)\s+(?:second|minute|hour|day|week|month|year)s?\s+ago\b|\bjust now\b",
    re.IGNORECASE,
)


def digest(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def value(content: dict, key: str, default: Any = "") -> Any:
    item = content.get(key, default)
    return item.get("value", default) if isinstance(item, dict) else item


def normalize_text(text: str) -> str:
    text = RELATIVE_TIME.sub("[relative time]", text)
    return "\n".join(line for line in (" ".join(x.split()) for x in text.splitlines()) if line)


def notes_snapshot(payload: dict, accepted: bool) -> dict:
    notes, count = payload.get("notes"), payload.get("count")
    if not isinstance(notes, list) or type(count) is not int or count < len(notes):
        raise ValueError("OpenReview returned an invalid notes/count response")
    sample = []
    for note in notes:
        if not isinstance(note, dict) or not note.get("id"):
            raise ValueError("Invalid public note")
        content = note.get("content", {})
        if accepted:
            if value(content, "venueid") != VENUE:
                raise ValueError("Accepted query returned a different venue/status")
        elif note.get("domain") != VENUE and not any(
            x.startswith(VENUE + "/") for x in note.get("invitations", [])
        ):
            raise ValueError("Activity query returned a note outside this conference")
        sample.append({
            "id": note["id"],
            "forum": note.get("forum") or note["id"],
            "title": str(value(content, "title"))[:180],
            "venue": str(value(content, "venue"))[:100],
            "content_hash": digest(content),
        })
    sample.sort(key=lambda item: item["id"])
    stable = {"count": count, "sample": sample}
    return {**stable, "hash": digest(stable)}


def fetch_notes(accepted: bool) -> dict:
    params = {"limit": 10, "sort": "tmdate:desc"}
    params["content.venueid" if accepted else "domain"] = VENUE
    retry = Retry(total=2, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=retry))
        response = session.get(API_URL, params=params, timeout=(10, 35), headers={
            "User-Agent": "Universal-Web-Monitoring-Agent/NeurIPS2026-public-watch",
            "Accept": "application/json",
        })
        if response.status_code == 403:
            detail = " ".join(response.text.split())[:500]
            raise RuntimeError(f"OpenReview public API denied this request (HTTP 403): {detail}")
        response.raise_for_status()
        return notes_snapshot(response.json(), accepted)


EXTRACT_PAGE = r"""() => {
    const root = document.querySelector('main, #content') || document.body;
    return {
        text: root.innerText || '',
        links: Array.from(root.querySelectorAll('a[href]')).filter(a =>
            a.getClientRects().length > 0 && getComputedStyle(a).visibility !== 'hidden'
        ).map(a => ({
            text: (a.innerText || '').trim(), href: a.href
        })).filter(a => a.href.startsWith('https://openreview.net/') &&
            (/\/forum\?/.test(a.href) || a.href.includes('#')))
    };
}"""


def fetch_page() -> dict:
    # Lazy import keeps offline regression tests independent of Chromium.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(locale="en-US", timezone_id="UTC")
            page = context.new_page()
            response = page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
            if response is None or response.status >= 400:
                raise RuntimeError(f"OpenReview page HTTP {response.status if response else 'unknown'}")
            # A plain HTML fetch only sees 'Loading'. Wait for the actual venue UI.
            try:
                page.wait_for_function(r"""() => {
                    const r = document.querySelector('main, #content') || document.body;
                    const t = r.innerText;
                    return /NeurIPS\s+2026/.test(t) && (
                        /No recent activity to display/i.test(t) ||
                        r.querySelector('a[href*="/forum?id="]') ||
                        Array.from(r.querySelectorAll('a,button,[role=tab]')).some(x =>
                            /^(accepted papers?|posters?|orals?|spotlights?|submissions?)(\s*\([\d,]+\))?$/i.test(x.innerText.trim()))
                    );
                }""", timeout=60000)
            except Exception as exc:
                visible = normalize_text(page.locator("body").inner_text(timeout=5000))[:1500]
                raise RuntimeError(f"Venue UI not ready; visible public page: {visible}") from exc
            # Require stable rendered text; don't fingerprint a half-loaded page.
            previous, stable_runs, snapshot = None, 0, None
            for _ in range(20):
                raw = page.evaluate(EXTRACT_PAGE)
                text = normalize_text(raw["text"])
                links = sorted({x["href"] for x in raw["links"]})
                snapshot = {"text": text, "links": links}
                current = digest(snapshot)
                stable_runs = stable_runs + 1 if current == previous else 0
                if stable_runs >= 2:
                    break
                previous = current
                page.wait_for_timeout(1000)
            else:
                raise RuntimeError("Rendered page did not stabilize; refusing partial snapshot")
            if len(snapshot["text"]) < 80 or not re.search(r"NeurIPS\s+2026", snapshot["text"]):
                raise RuntimeError("Rendered page is not the expected NeurIPS 2026 venue")
            snapshot["paper_links"] = [x for x in snapshot["links"] if "/forum?" in x]
            snapshot["hash"] = digest({"text": snapshot["text"], "links": snapshot["links"]})
            return snapshot
        finally:
            browser.close()


def compare(previous: dict, current: dict) -> tuple[list[str], dict]:
    """Return alert reasons and merged good snapshots, preserving failed sources."""
    reasons, merged = [], dict(previous)
    labels = {
        "page": "Gerenderte OpenReview-Seite: Inhalt, Tabs oder Paper-Links geändert.",
        "accepted": "Öffentlich abrufbare Accepted-Paper-Daten geändert.",
        "activity": "Öffentliche OpenReview-Aktivität geändert.",
    }
    for source, snapshot in current.items():
        old = previous.get(source)
        # Don't suppress a release already visible on the very first run.
        initial_hit = snapshot.get("count", 0) > 0 or bool(snapshot.get("paper_links"))
        if (old is not None and old["hash"] != snapshot["hash"]) or (old is None and initial_hit):
            reasons.append(labels[source])
        merged[source] = snapshot
    return reasons, merged


def render_message(current: dict, reasons: list[str], errors: list[str], now: str) -> tuple[str, str]:
    accepted = current.get("accepted")
    count = accepted.get("count") if accepted is not None else None
    confirmed = count is not None and count > 0
    subject = "NeurIPS 2026: Accepted Papers öffentlich" if confirmed else "NeurIPS 2026: OpenReview geändert"
    status = (
        f"Accepted Papers öffentlich abrufbar: {count}. Der Bestand kann noch wachsen."
        if confirmed else
        "Accepted-Paper-Veröffentlichung noch nicht bestätigt; siehe Seitenänderung."
    )
    lines = [subject, PAGE_URL, "", status, *reasons, "", f"Geprüft: {now}"]
    if errors:
        lines.append("Achtung: Ein Teil der Checks war nicht erreichbar; Details im GitHub-Bericht.")
    return subject, "\n".join(lines)[:950] + "\n"


def output(**items: Any) -> None:
    if path := os.getenv("GITHUB_OUTPUT"):
        with open(path, "a", encoding="utf-8") as handle:
            for key, item in items.items():
                handle.write(f"{key}={item}\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alert-file", required=True)
    args = parser.parse_args(argv)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"schema_version": 1, "sources": {}}
    if state.get("schema_version") != 1 or not isinstance(state.get("sources"), dict):
        raise ValueError("Invalid saved watch state; refusing to reset comparison history")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    current, errors = {}, []
    # Independent checks: a browser outage must not hide a public API release.
    for name, fetcher in (("accepted", lambda: fetch_notes(True)), ("activity", lambda: fetch_notes(False)), ("page", fetch_page)):
        try:
            print(f"Checking public source: {name}", flush=True)
            current[name] = fetcher()
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:1600]}")
    reasons, merged = compare(state["sources"], current)
    subject, message = render_message(current, reasons, errors, now)
    lines = ["# NeurIPS 2026 — public OpenReview watch", "", f"Checked: {now}", PAGE_URL, ""]
    for name, snapshot in current.items():
        detail = f"public notes: {snapshot['count']}" if "count" in snapshot else f"rendered paper links: {len(snapshot['paper_links'])}"
        lines.append(f"- {name}: OK; {detail}; fingerprint {snapshot['hash'][:12]}")
    lines += ["", "## Changes", *(reasons or ["No new change detected in successfully checked sources."])]
    if errors:
        lines += ["", "## Incomplete checks (not an all-clear)", *errors]
    report = "\n".join(lines) + "\n"
    print(report)
    if summary := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report)
    if reasons:
        Path(args.alert_file).write_text(message, encoding="utf-8")
    else:
        Path(args.alert_file).unlink(missing_ok=True)
    # No heartbeat commits every five minutes; job summaries contain every check.
    changed = merged != state["sources"]
    if changed:
        STATE.write_text(json.dumps({"schema_version": 1, "updated_at": now, "sources": merged}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if changed or errors or not REPORT.exists():
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(report, encoding="utf-8")
    output(alert_count=len(reasons), error_count=len(errors), subject=subject)
    # The workflow sends/persists successful checks first, then marks errors red.
    if not os.getenv("GITHUB_OUTPUT") and errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
