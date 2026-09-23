"""Categorized daily alerts and a persistent seven-day third-party digest.

The collector remains in music_jepa_watch.py. This is the workflow entry point.
State is staged locally; the workflow must send BOTH emails before pushing it.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import music_jepa_watch as core

DIRECT = "direct"
AUTHOR = "author"
THIRD = "third_party"
TECHNICAL = "technical"
INTERVAL = timedelta(days=7)
DIRECT_KEYS = {"arxiv", "project-page", "project-source"}


def timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("A timezone-aware clock is required")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Digest timestamp has no timezone")
    return result


def delivery_state(state: dict, now: datetime) -> dict:
    """Add delivery metadata without resetting any existing observations."""
    if "delivery" not in state:
        state["delivery"] = {
            "version": 1, "pending": {}, "categories": {},
            "next_digest_at": timestamp(now + INTERVAL), "last_digest_at": None,
        }
    data = state["delivery"]
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unknown delivery state; refusing to reset the digest")
    if not isinstance(data.get("pending"), dict) or not isinstance(data.get("categories"), dict):
        raise ValueError("Invalid digest queue/categories")
    parse_time(data["next_digest_at"])
    if data.get("last_digest_at"):
        parse_time(data["last_digest_at"])
    return data


def resource_key(url: str) -> str | None:
    for host, prefix in (("github.com", "repo:"), ("huggingface.co", "hf:")):
        name = core.resource_id(url, host)
        if name:
            return prefix + name.lower()
    return None


def examples(items, limit: int = 3) -> str:
    values = sorted(str(x) for x in items)
    return ", ".join(values[:limit]) + (f" (+{len(values) - limit} weitere)" if len(values) > limit else "")


def describe_change(key: str, old: dict | None, new: dict) -> list[str]:
    before = old or {}
    lines = []
    if key == "arxiv":
        if old is None or before.get("version") != new.get("version"):
            previous = f"v{before['version']}" if "version" in before else "bisher nicht erfasst"
            lines.append(f"Paper-Version: {previous} → v{new['version']}.")
        else:
            lines.append(f"Paper weiterhin v{new['version']}; Ressourcenlinks geändert.")
    elif key == "project-page":
        lines.append("Das HTML der offiziellen Projektseite hat sich geändert.")
    elif key == "project-source":
        lines.append("Dateien der offiziellen Demo-Website wurden geändert (einschließlich Medien/Assets).")
    elif key.startswith("repo:"):
        files = new.get("code_files", [])
        added = set(files) - set(before.get("code_files", []))
        removed = set(before.get("code_files", [])) - set(files)
        if old is None:
            lines.append(f"Erstmals erfasst: {len(files)} Code-Dateien" + (f"; Beispiele: {examples(files)}." if files else "."))
        elif added or removed:
            lines.append(f"Code-Dateien: {len(added)} neu, {len(removed)} entfernt; jetzt {len(files)} insgesamt.")
            if added:
                lines.append(f"Neue Dateien, Beispiele: {examples(added)}.")
        weights = new.get("weight_file_hints", {})
        if old is None or weights != before.get("weight_file_hints", {}):
            lines.append(f"Gewichtsdatei-Hinweise im Repository: {len(weights)}" + (f" ({examples(weights)})." if weights else ". Kein Gewichte-Download damit belegt."))
        releases = new.get("releases", [])
        if old is None or core.digest(releases) != core.digest(before.get("releases", [])):
            lines.append(f"GitHub-Releases: {len(releases)}; " + ("Release-Liste oder Anhänge neu/geändert." if releases else "keine erfasst."))
    elif key.startswith("hf:"):
        files = new.get("weight_file_hints", [])
        lines.append(f"Gewichtsdatei-Hinweise auf Hugging Face: {len(files)}" + (f" ({examples(files)})." if files else "."))
        gate = new.get("gated", False)
        lines.append(f"Zugangsfreigabe (Gating): {gate}. Download und Nutzbarkeit nicht getestet.")
    link_field = "links" if key in ("arxiv", "project-page") else "resource_links"
    added_links = set(new.get(link_field, [])) - set(before.get(link_field, []))
    removed_links = set(before.get(link_field, [])) - set(new.get(link_field, []))
    if added_links:
        lines.append(f"Neue Ressourcenlinks: {examples(added_links)}")
    if removed_links:
        lines.append(f"Entfernte Ressourcenlinks: {examples(removed_links)}")
    return lines or ["Die gespeicherten Ressourcenhinweise wurden geändert."]


class CategorizedWatcher(core.Watcher):
    def __init__(self, client, state: dict, now: datetime):
        super().__init__(client, state)
        self.delivery = delivery_state(state, now)
        self.provenance: dict[str, str] = {}
        # Previously fetched PRIMARY links remain evidence during an outage.
        for key, source in (("arxiv", core.ARXIV), ("project-page", core.PROJECT)):
            self.remember_links(state["observations"].get(key, {}).get("links", []), source)
        self.provenance["repo:" + core.PROJECT_REPO.lower()] = core.PROJECT

    def remember_links(self, links: list[str], source: str):
        for link in links:
            key = resource_key(link)
            if key:
                self.provenance[key] = source

    def category(self, key: str) -> tuple[str, str]:
        if key in DIRECT_KEYS:
            return DIRECT, "Direkte Überwachung der Paper-/Projektquelle; kein Suchtreffer."
        if key.startswith("repo:") and key[5:].split("/")[0].lower() in {a.lower() for a in core.AUTHORS}:
            return AUTHOR, f"Repository im beobachteten Autorenkonto {key[5:].split('/')[0]}."
        if key.lower() in self.provenance:
            return AUTHOR, f"Ressource verlinkt aus: {self.provenance[key.lower()]}"
        if key.startswith("repo:"):
            return THIRD, "GitHub-Suche: Bezug auf Paper-ID/Titel in Repository-Metadaten oder README; offizielle Zuordnung unbestätigt."
        return THIRD, "Hugging-Face-Suche/entdeckter Modelllink; offizielle Zuordnung unbestätigt."

    def observe(self, key: str, value: dict, title: str, url: str, initial: bool = False):
        before = copy.deepcopy(self.state["observations"].get(key))
        start = len(self.events)
        super().observe(key, value, title, url, initial=initial)
        category, origin = self.category(key)
        previous_category = self.delivery["categories"].get(key)
        # A newly established project link must not wait for the weekly digest.
        if previous_category == THIRD and category == AUTHOR and len(self.events) == start:
            self.events.append({"title": title, "url": url, "detail": "Neue Zuordnung zur Autoren-/Projektquelle."})
        self.delivery["categories"][key] = category
        if category != THIRD:
            self.delivery["pending"].pop(key, None)
        for event in self.events[start:]:
            event.update(key=key, category=category, origin=origin,
                         summary=describe_change(key, before, value),
                         fingerprint=core.digest([key, before, value, category]))
            if previous_category == THIRD and category == AUTHOR:
                event["summary"].insert(0, "Neu: Verknüpfung mit einer beobachteten Autoren-/Projektquelle erkannt.")

    def paper(self):
        super().paper()
        self.remember_links(self.state["observations"]["arxiv"]["links"], core.ARXIV)

    def project(self):
        super().project()
        self.remember_links(self.state["observations"]["project-page"]["links"], core.PROJECT)

    def inspect_repo(self, meta: dict, linked: bool = False):
        super().inspect_repo(meta, linked=linked)
        key = "repo:" + meta["full_name"]
        # Links from a third-party README NEVER acquire primary provenance.
        if key in self.state["observations"] and self.category(key)[0] == AUTHOR:
            self.remember_links(self.state["observations"][key].get("resource_links", []),
                                "https://github.com/" + meta["full_name"])

    def attempt(self, name: str, fn):
        start = len(self.events)
        result = super().attempt(name, fn)
        for event in self.events[start:]:
            if "category" not in event:
                event.update(key="error:" + name, category=TECHNICAL,
                             origin="Technischer Abruffehler; kein neuer Release-Hinweis.",
                             summary=[event["detail"]])
        return result


def route(watcher: CategorizedWatcher, now: datetime) -> dict:
    """Prepare deliveries. Persist the resulting state only AFTER SMTP success."""
    data = watcher.delivery
    daily = []
    for event in watcher.events:
        if event["category"] != THIRD:
            daily.append(event)
            continue
        key = event["key"]
        queued = data["pending"].setdefault(key, {
            "key": key, "title": event["title"], "url": event["url"], "origin": event["origin"],
            "first_seen_at": timestamp(now), "last_seen_at": timestamp(now), "changes": [],
        })
        queued["last_seen_at"] = timestamp(now)
        if event["fingerprint"] not in {x["fingerprint"] for x in queued["changes"]}:
            queued["changes"].append({"fingerprint": event["fingerprint"], "summary": event["summary"]})
    weekly = []
    due = now >= parse_time(data["next_digest_at"])
    if data.get("last_digest_at"):
        due = due and now >= parse_time(data["last_digest_at"]) + INTERVAL
    if due and data["pending"]:
        weekly = copy.deepcopy([data["pending"][k] for k in sorted(data["pending"])])
        data["pending"].clear()
        data["last_digest_at"] = timestamp(now)
        data["next_digest_at"] = timestamp(now + INTERVAL)
    return {"daily": daily, "weekly": weekly}


def event_lines(events: list[dict]) -> list[str]:
    result = []
    for event in events:
        result += [f"### {event['title']}", event["url"], f"Herkunft: {event['origin']}"]
        result += [f"- {line}" for line in event["summary"]]
        result.append("")
    return result


def direct_status(watcher: CategorizedWatcher) -> list[str]:
    changed = {e["key"] for e in watcher.events if e["category"] == DIRECT}
    rows = []
    for label, key, check in (("arXiv-Paper", "arxiv", "arXiv"),
                              ("Offizielle Projektseite", "project-page", "Projektseite"),
                              ("Dateien der offiziellen Demo-Website", "project-source", "GitHub Repository " + core.PROJECT_REPO)):
        if key in changed:
            status = "ÄNDERUNG ERKANNT — klarer Treffer an direkt überwachter Quelle"
        elif check in watcher.failed or check not in watcher.checked:
            status = "NICHT VOLLSTÄNDIG GEPRÜFT — keine Aussage über Änderungen"
        else:
            status = "keine neue Änderung erkannt (Erstabruf setzt nur den Vergleichsstand)"
        rows.append(f"{label}: {status}.")
    return rows


def daily_subject(events: list[dict]) -> str:
    for category, label in ((DIRECT, "DIREKTE QUELLENÄNDERUNG"), (AUTHOR, "AUTOREN-/PROJEKTHINWEIS"), (TECHNICAL, "TECHNISCHE STÖRUNG")):
        count = sum(e["category"] == category for e in events)
        if count:
            return f"[Web-Monitor] Music-JEPA | {label} ({count})"
    return ""


def render_daily(watcher: CategorizedWatcher, events: list[dict], now: datetime) -> str:
    lines = ["# Music-JEPA — täglicher Alarm", timestamp(now), "",
             "Klare Quellenänderung bedeutet NICHT automatisch: Code und Gewichte veröffentlicht.", "",
             "## Status der direkt überwachten Quellen", *direct_status(watcher), ""]
    for category, title in ((DIRECT, "A. KLARE TREFFER — direkte Paper-/Projektänderungen"),
                             (AUTHOR, "B. AUTOREN-/PROJEKTHINWEISE — kein bestätigter Release"),
                             (TECHNICAL, "C. TECHNISCHE STÖRUNGEN — keine Forschungstreffer")):
        selected = [e for e in events if e["category"] == category]
        lines += [f"## {title}", f"Neue Meldungen: {len(selected)}.", ""] + event_lines(selected)
    lines += ["Drittanbieter-Suchtreffer lösen diese Tagesmail nicht aus. Sie werden separat,",
              "frühestens alle sieben Tage und nur bei neuen Hinweisen als Wochensammlung verschickt.", ""]
    return "\n".join(lines)


def render_weekly(entries: list[dict], now: datetime) -> str:
    lines = ["# Music-JEPA — WOCHENSAMMLUNG: DRITTANBIETER", timestamp(now), "",
             f"{len(entries)} Repository-/Modellquellen mit noch nicht gemeldeten Hinweisen.",
             "Dies sind Suchfunde, KEINE bestätigten offiziellen Releases und KEINE Alarme über",
             "eine neue Paper-Version oder eine geänderte offizielle Projektseite.",
             "Direkte Paper-/Projektänderungen werden unabhängig davon im täglichen Lauf gemeldet.", ""]
    for entry in entries:
        lines += [f"## {entry['key'].split(':', 1)[-1]}", entry["url"], f"Herkunft: {entry['origin']}",
                  f"Gesammelt: {entry['first_seen_at']} bis {entry['last_seen_at']}"]
        summaries = dict.fromkeys(line for change in entry["changes"] for line in change["summary"])
        lines += [f"- {line}" for line in summaries] + [""]
    lines += ["Dateinamen sind nur Hinweise. Code/Modelle wurden weder ausgeführt noch heruntergeladen.", ""]
    return "\n".join(lines)


def render_report(watcher: CategorizedWatcher, batches: dict, now: datetime) -> str:
    third = [e for e in watcher.events if e["category"] == THIRD]
    lines = ["# Music-JEPA — Prüfbericht", timestamp(now), "",
             f"Tagesalarm: {len(batches['daily'])} Meldungen.",
             f"Neue Drittanbieter-Signale dieses Laufs: {len(third)} (kein Tagesalarm).",
             f"Wochensammlung dieses Laufs: {len(batches['weekly'])} Quellen.",
             f"Für spätere Wochensammlung vorgemerkt: {len(watcher.delivery['pending'])} Quellen.",
             f"Nächste Wochensammlung frühestens: {watcher.delivery['next_digest_at']} (nächster Lauf; nur mit neuen Hinweisen).",
             f"Abruffehler in diesem Lauf: {len(watcher.failed)}.", "",
             render_daily(watcher, batches["daily"], now),
             "## D. DRITTANBIETER-SUCHE — ausschließlich für Wochensammlung", *event_lines(third)]
    if batches["weekly"]:
        lines.append(render_weekly(batches["weekly"], now))
    lines += ["## Technischer Quellenstatus", *[f"OK: {x}" for x in watcher.checked],
              *[f"FEHLER: {k}: {v}" for k, v in watcher.failed.items()], ""]
    return "\n".join(lines)


def write_mail(path: Path | None, body: str, enabled: bool):
    if path:
        if enabled:
            path.write_text(body, encoding="utf-8")
        else:
            path.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, default=Path("seen_music_jepa.json"))
    parser.add_argument("--report-file", type=Path, default=Path("reports/music_jepa_latest.md"))
    parser.add_argument("--alert-file", type=Path, help="Daily email body; never contains third-party entries")
    parser.add_argument("--weekly-alert-file", type=Path, help="Separate weekly digest body")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    state = json.loads(args.state_file.read_text(encoding="utf-8")) if args.state_file.exists() else core.new_state()
    if state.get("schema_version") != 1 or state.get("paper_id") != core.PAPER:
        raise ValueError("State schema/paper mismatch; refusing to reset baselines")
    for field in ("observations", "repo_cache", "errors"):
        if not isinstance(state.get(field), dict):
            raise ValueError(f"Invalid state field: {field}")
    now = datetime.now(timezone.utc)
    watcher = CategorizedWatcher(core.Client(), state, now)
    watcher.run()
    batches = route(watcher, now)
    daily, weekly = batches["daily"], batches["weekly"]
    # Never consume a due digest in a non-dry run without producing its mail.
    if daily and not args.alert_file and not args.dry_run:
        raise ValueError("A daily alert requires --alert-file; state was not saved")
    if weekly and not args.weekly_alert_file and not args.dry_run:
        raise ValueError("A due digest requires --weekly-alert-file; state was not saved")
    report = render_report(watcher, batches, now)
    print(report)
    write_mail(args.alert_file, render_daily(watcher, daily, now), bool(daily))
    write_mail(args.weekly_alert_file, render_weekly(weekly, now), bool(weekly))
    if not args.dry_run:
        state["last_checked"] = timestamp(now)
        args.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(args.state_file)
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        args.report_file.write_text(report, encoding="utf-8")
    output = (f"daily_count={len(daily)}\nweekly_count={len(weekly)}\n"
              f"pending_count={len(watcher.delivery['pending'])}\nerror_count={len(watcher.failed)}\n"
              f"daily_subject={daily_subject(daily)}\n"
              f"weekly_subject=[Web-Monitor] Music-JEPA | WOCHENSAMMLUNG: DRITTANBIETER ({len(weekly)})\n")
    for env, content in (("GITHUB_OUTPUT", output), ("GITHUB_STEP_SUMMARY", report)):
        if os.getenv(env):
            with open(os.environ[env], "a", encoding="utf-8") as out:
                out.write(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
