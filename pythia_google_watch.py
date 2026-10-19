"""Daily Google web search for EleutherAI Pythia, with a durable SMTP outbox.

Uses SerpApi's Google engine (not Google News). The workflow persists collection
BEFORE calling deliver, and persists delivery acknowledgements even on failure.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import requests

import send_alert

REPO = "https://github.com/Erikiss/Universal-Web-Monitoring-Agent"
REPORT_URL = REPO + "/blob/main/reports/pythia_google_latest.md"
# Keep the digest within Pushover's message limit; never split it into alerts.
DIGEST_MAX_CHARS = 1024
FOCUSED_QUERY = 'pythia (EleutherAI OR "GPT-NeoX" OR "language model" OR Sprachmodell OR "mechanistic interpretability")'
NAME = re.compile(r"(?<![a-z0-9])pythia(?![a-z0-9])", re.I)
MODEL_SIZE = re.compile(r"\bpythia[\s_/-]*(?:14m|31m|70m|160m|410m|1b|1[.,]4b|2[.,]8b|6[.,]9b|12b)(?![a-z0-9])", re.I)
IDENTITY = re.compile(r"eleuther[\s-]*ai|gpt[\s_-]*neox|2304\.01373", re.I)
LLM = re.compile(r"\b(?:llms?|language models?|sprachmodel\w*|modèles? de langage|"
                 r"mechanistic interpretability|mechanistische interpretierbarkeit|"
                 r"large language|transformerlens|语言模型|言語モデル)\b", re.I)
OTHER = re.compile(r"\b(?:pythia[\s_-]*8(?:\b|\.)|cern|hadron\w*|parton\w*|"
                   r"particle (?:physics|collision|shower)|event generator|"
                   r"teilchenphysik|monte[\s-]+carlo|pythia8|delphi|oracle of|"
                   r"prefetch\w*|robotics|robotic|crypto\w*)\b", re.I)
TRACKING = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid"}


class SearchFailure(RuntimeError):
    """Only safe, fixed diagnostic text: never raw API responses or request URLs."""


def stamp(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("Timezone-aware clock required")
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("State timestamp is missing its timezone")
    return result


def compact(value: str) -> str:
    return " ".join(value.split())


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("Invalid result URL")
        host = parts.hostname.lower()
        port = parts.port
        netloc = host + (f":{port}" if port and port not in {80, 443} else "")
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
        path = parts.path or "/"
        if host in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            match = re.fullmatch(r"/(?:abs|pdf|html)/(.+?)(?:\.pdf)?/?", path)
            if match:
                return "https://arxiv.org/abs/" + match[1]
        return urlunsplit((parts.scheme, netloc, path, urlencode(sorted(query)), ""))
    except (TypeError, ValueError):
        raise SearchFailure("Ungültige URL in Suchergebnissen") from None


def classify(item: dict) -> tuple[str, str]:
    # Query terms are NEVER evidence: use only the returned title/snippet/URL.
    text = compact(" ".join(item.get(k, "") for k in ("title", "snippet", "url")))
    text = unquote(text)
    if not NAME.search(text):
        return "unclear", "Pythia-Bezug im Suchauszug nicht belegt"
    if MODEL_SIZE.search(text) or IDENTITY.search(text):
        return "match", "EleutherAI-/GPT-NeoX-Bezug oder konkrete Pythia-Modellgröße"
    if OTHER.search(text):
        return "excluded", "Hinweis auf ein anderes Pythia (z. B. Teilchenphysik)"
    for mention in NAME.finditer(text):
        context = text[max(0, mention.start() - 250):mention.end() + 350]
        if LLM.search(context):
            return "match", "Pythia im Sprachmodell-/Interpretierbarkeits-Kontext"
    return "unclear", "Kein ausreichender Beleg für das EleutherAI-Modell im Suchauszug"


def new_state(now: datetime) -> dict:
    return {"schema_version": 1, "started_at": stamp(now), "last_success_at": None,
            "pending": {}, "delivered": {}}


def validate_state(state: dict) -> dict:
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError("Invalid Pythia state version; refusing to reset history")
    parse_time(state["started_at"])
    if state.get("last_success_at"):
        parse_time(state["last_success_at"])
    for key in ("pending", "delivered"):
        if not isinstance(state.get(key), dict):
            raise ValueError("Invalid Pythia outbox/history")
        for url, entry in state[key].items():
            if canonical_url(url) != url or not isinstance(entry, dict) or entry.get("url") != url:
                raise ValueError("Invalid Pythia history entry")
            if not isinstance(entry.get("title"), str) or not entry["title"]:
                raise ValueError("Missing Pythia result title")
    if state["pending"].keys() & state["delivered"].keys():
        raise ValueError("Pythia outbox overlaps delivery history")
    return state


def load_state(path: Path, now: datetime) -> dict:
    return validate_state(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else new_state(now)


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def search_plan(state: dict, now: datetime) -> list[dict]:
    since = parse_time(state.get("last_success_at") or state["started_at"])
    if since > now:
        raise ValueError("Pythia state is ahead of the current clock")
    # Three days normally; after an outage cover the entire gap plus two days.
    # No cap which could silently discard an outage's older results.
    days = max(3, math.ceil((now - since).total_seconds() / 86400) + 2)
    return [{"label": "24 Stunden", "q": "pythia", "tbs": "qdr:d"},
            {"label": f"Überlappung/Nachholen: {days} Tage", "q": "pythia", "tbs": f"qdr:d{days}"},
            {"label": f"Ergänzende Modellsuche: {days} Tage", "q": FOCUSED_QUERY, "tbs": f"qdr:d{days}"}]


class GoogleSearch:
    def __init__(self, api_key: str, max_pages: int = 10, session=None):
        if not api_key:
            raise SearchFailure("Repository-Secret SERPAPI_API_KEY fehlt (Alias SERPAPI_KEY wird ebenfalls unterstützt)")
        if not 1 <= max_pages <= 100:
            raise ValueError("PYTHIA_MAX_PAGES must be between 1 and 100")
        self.api_key, self.max_pages = api_key, max_pages
        self.deadline = time.monotonic() + 12 * 60
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "PythiaGoogleWatch/1.0 (+" + REPO + ")"})

    def request(self, query: dict, start: int) -> dict:
        params = {"engine": "google", "q": query["q"], "tbs": query["tbs"],
                  "google_domain": "google.com", "hl": "de", "nfpr": "1",
                  "start": start, "api_key": self.api_key}
        for attempt in range(3):
            remaining = self.deadline - time.monotonic()
            if remaining < 20:
                raise SearchFailure("Zeitbudget für Google-Suche erschöpft; Zeitmarke bleibt erhalten")
            try:
                response = self.session.get("https://serpapi.com/search.json", params=params,
                                            timeout=(10, min(45, remaining - 15)))
            except requests.RequestException:
                if attempt == 2:
                    raise SearchFailure("Such-API: Netzwerkfehler nach drei Versuchen") from None
                time.sleep(2 ** (attempt + 1))
                continue
            if response.status_code in {502, 503, 504} and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            if response.status_code != 200:
                raise SearchFailure(f"Such-API: HTTP {response.status_code}; Suche nicht vollständig")
            try:
                data = response.json()
            except ValueError:
                raise SearchFailure("Such-API liefert kein gültiges JSON") from None
            if not isinstance(data, dict):
                raise SearchFailure("Such-API liefert ein unbekanntes Antwortformat")
            return data
        raise SearchFailure("Such-API nicht erreichbar")

    def pages(self, query: dict):
        start, signatures = 0, set()
        for _ in range(self.max_pages):
            data = self.request(query, start)
            if data.get("search_metadata", {}).get("status") != "Success":
                raise SearchFailure("Such-API meldet keine erfolgreiche Suche")
            echoed = data.get("search_parameters", {})
            if any(echoed.get(k) != query[k] for k in ("q", "tbs")) or echoed.get("engine") != "google":
                raise SearchFailure("Such-API bestätigt Suchbegriff/Zeitfilter/Google-Engine nicht")
            info = data.get("search_information", {})
            empty = (info.get("organic_results_state") == "Fully empty" or info.get("total_results") == 0)
            if data.get("error") and not (empty and data["error"] == "Google hasn't returned any results for this query."):
                raise SearchFailure("Such-API meldet einen Fehler; kein Keine-Treffer-Ergebnis")
            rows = data.get("organic_results")
            if rows is None and empty:
                rows = []
            if not isinstance(rows, list) or (not rows and not empty):
                raise SearchFailure("Fehlende organische Ergebnisse ohne bestätigtes Leer-Ergebnis")
            results = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("title"), str) or not row["title"].strip():
                    raise SearchFailure("Unvollständiger Suchtreffer ohne Titel")
                if not isinstance(row.get("link"), str) or not isinstance(row.get("snippet", ""), str):
                    raise SearchFailure("Ungültiger Suchtreffer")
                results.append({"title": compact(row["title"]), "url": canonical_url(row["link"]),
                                "snippet": compact(row.get("snippet", "")),
                                "google_date": compact(str(row.get("date", "")))})
            signature = tuple(sorted(row["url"] for row in results))
            if results and signature in signatures:
                raise SearchFailure("Such-API wiederholt eine Ergebnisseite; Pagination unvollständig")
            signatures.add(signature)
            yield results
            paging = data.get("serpapi_pagination") or data.get("pagination") or {}
            next_url = paging.get("next") or paging.get("next_link")
            if not next_url:
                return
            # Never follow an API-provided URL with our secret. Extract only the
            # numeric offset, then reconstruct the request to the fixed endpoint.
            try:
                next_start = int(dict(parse_qsl(urlsplit(next_url).query))["start"])
            except (TypeError, ValueError, KeyError):
                raise SearchFailure("Ungültiger Pagination-Offset") from None
            if next_start <= start or not results:
                raise SearchFailure("Pagination macht keinen Fortschritt")
            start = next_start
        raise SearchFailure("PYTHIA_MAX_PAGES erreicht, weitere Treffer vorhanden; Zeitmarke bleibt erhalten")


def collect(client: GoogleSearch, old: dict, now: datetime) -> tuple[dict, dict]:
    state = copy.deepcopy(validate_state(old))
    queries = search_plan(state, now)
    audit, errors, count = [], [], 0
    for query in queries:
        try:
            for items in client.pages(query):
                for item in items:
                    category, reason = classify(item)
                    audit.append({**item, "classification": category, "reason": reason, "search": query["label"]})
                    url = item["url"]
                    if category != "match" or url in state["delivered"]:
                        continue
                    if url not in state["pending"]:
                        state["pending"][url] = {**item, "reason": reason, "first_seen_at": stamp(now)}
                        count += 1
        except SearchFailure as exc:
            errors.append(query["label"] + ": " + str(exc))
    if not errors:
        state["last_success_at"] = stamp(now)
    state["last_attempt_at"] = stamp(now)
    state["last_errors"] = errors
    report = {"checked_at": stamp(now), "queries": queries, "results": audit, "errors": errors,
              "new_count": count, "pending_count": len(state["pending"]),
              "pending_results": copy.deepcopy(list(state["pending"].values())),
              "last_success_at": state["last_success_at"]}
    return state, report


def render_report(report: dict) -> str:
    lines = ["# Pythia – tägliche Google-Websuche", "", report["checked_at"], "",
             f"Neu vorgemerkt: {report['new_count']} | Noch zu versenden: {report['pending_count']}",
             f"Letzte vollständig erfolgreiche Suche: {report['last_success_at'] or 'noch keine'}", "",
             "Google-Websuche via SerpApi; keine Beschränkung auf News, Dateityp oder Quellsprache.",
             "Google-Datumsfilter sind keine vollständige Publikationsdatenbank. Titel/Snippets werden heuristisch geprüft.",
             "Unklare Treffer werden dokumentiert und nicht als bereits gemeldet gespeichert.", ""]
    for query in report["queries"]:
        link = "https://www.google.com/search?" + urlencode({"q": query["q"], "tbs": query["tbs"]})
        lines += [f"- {query['label']}: {link}"]
    if report["errors"]:
        lines += ["", "## Unvollständige Suche", *["- " + e for e in report["errors"]]]
    # Include the entire outbox, even hits absent from today's search results.
    # This report is persisted before sending the compact digest.
    lines += ["", "## Sammelmeldung – vorgemerkte Treffer dieses Laufs", ""]
    for row in report.get("pending_results", []):
        lines += [compact(row["title"]), row["url"], row.get("snippet", ""),
                  "Erstmals gefunden: " + row["first_seen_at"], ""]
    if not report.get("pending_results"):
        lines += ["Keine vorgemerkten Treffer."]
    for category, heading in (("match", "Modelltreffer"), ("unclear", "Unklar – zur Nachprüfung"), ("excluded", "Andere Pythias")):
        rows = {r["url"]: r for r in report["results"] if r["classification"] == category}
        lines += ["", "## " + heading, ""]
        for row in rows.values():
            lines += [compact(row["title"]), row["url"], row["snippet"],
                      "Einordnung: " + row["reason"], ""]
        if not rows:
            lines += ["Keine in den erfolgreich abgerufenen Ergebnisseiten."]
    return "\n".join(lines) + "\n"


def render_digest(items: list[dict]) -> str:
    body = (f"Neue Pythia-Treffer (EleutherAI): {len(items)}\n\n"
            f"Alle Treffer und Details:\n{REPORT_URL}\n\n")
    shown = 0
    for index, item in enumerate(items, 1):
        entry = f"{index}. {compact(item['title'])[:120]}\n{item['url']}\n\n"
        # Reserve room to explain omitted entries, without cutting any URL.
        remaining = len(items) - index
        suffix = f"Weitere Treffer im Prüfbericht: {remaining}\n" if remaining else ""
        if len(body + entry + suffix) > DIGEST_MAX_CHARS:
            break
        body += entry
        shown = index
    if shown < len(items):
        body += f"Weitere Treffer im Prüfbericht: {len(items) - shown}\n"
    return body


def deliver_pending(path: Path, now: datetime, sender=None) -> int:
    """Send the whole outbox once; return the number of emails (zero or one)."""
    state = load_state(path, now)
    pending = state["pending"]
    if not pending:
        return 0
    sender = sender or send_alert.main
    body = render_digest(list(pending.values()))
    with tempfile.TemporaryDirectory(prefix="pythia-mail-") as directory:
        mail = Path(directory) / "alert.txt"
        mail.write_text(body, encoding="utf-8")
        # A failed send leaves every hit pending. There is no per-hit send or
        # in-process retry that could produce a burst of Pushover alerts.
        sender(["--subject", f"[Web-Monitor] Pythia | Sammelmeldung ({len(pending)} Treffer)",
                "--body-file", str(mail)])
    for url, item in pending.items():
        state["delivered"][url] = {**item, "delivered_at": stamp(now)}
    state["pending"] = {}
    save_json(path, state)
    return 1


def write_summary(text: str) -> None:
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect", "deliver"])
    parser.add_argument("--state", type=Path, default=Path("seen_pythia_google.json"))
    parser.add_argument("--report", type=Path, default=Path("reports/pythia_google_latest.md"))
    parser.add_argument("--archive", type=Path, default=Path("reports/pythia-google"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    if args.command == "deliver":
        if args.dry_run:
            print("Dry run: no email sent or state changed.")
            return 0
        print(f"Pythia: {deliver_pending(args.state, now)} email(s) accepted by SMTP.")
        return 0
    old = load_state(args.state, now)
    # Absence of a key must be visible and must not initialize/advance state.
    try:
        client = GoogleSearch(os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY", ""),
                              int(os.getenv("PYTHIA_MAX_PAGES") or "10"))
    except SearchFailure as exc:
        write_summary("## Pythia Google watch: Einrichtung fehlt\n\n" + str(exc) + "\n\n"
                      "Vorhandene SMTP-Secrets bleiben unverändert. Anleitung: " + REPO + "/blob/main/PYTHIA_GOOGLE_WATCH.md\n")
        print(str(exc), file=sys.stderr)
        return 1
    state, report = collect(client, old, now)
    rendered = render_report(report)
    if not args.dry_run:
        save_json(args.state, state)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
        # Append-only run archive: repeated runs on the same day cannot erase
        # discoveries or ambiguous candidates from an earlier run.
        run_id = now.strftime("%Y%m%dT%H%M%S%fZ")
        save_json(args.archive / (run_id + ".json"), report)
    write_summary(rendered)
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"error_count={len(report['errors'])}\npending_count={len(state['pending'])}\n")
    print(f"Pythia: {report['new_count']} new, {len(state['pending'])} pending, {len(report['errors'])} search errors; dry_run={args.dry_run}")
    # Actions must persist partial results/outbox before its final failure step.
    return 0 if os.getenv("GITHUB_OUTPUT") else int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
