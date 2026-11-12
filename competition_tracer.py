"""Extensible daily competition/research tracer; never executes submitted code."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser

import requests

USER_AGENT = "UniversalWebMonitoringAgent/CompetitionTracer (+https://github.com/Erikiss/Universal-Web-Monitoring-Agent)"
ROOT_URL = "https://github.com/Erikiss/Universal-Web-Monitoring-Agent/blob/main/reports/competitions/latest.md"


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json(path, default):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else copy.deepcopy(default)


class Client:
    """Bounded HTTP with origin-scoped credentials, pacing and explicit failures."""

    def __init__(self, budget_seconds=900):
        self.session = requests.Session()
        self.deadline = time.monotonic() + budget_seconds
        self.last_request = {}
        self.robots = {}

    def _request(self, url, params=None):
        for redirect in range(4):
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Only credential-free HTTPS source URLs are supported")
            host = parsed.hostname
            headers = {"User-Agent": USER_AGENT}
            if host == "api.github.com":
                headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
                token = os.getenv("GITHUB_TOKEN")
                if token:
                    headers["Authorization"] = "Bearer " + token
            delay = 3.1 if "arxiv.org" in host else (0.15 if host == "api.github.com" else 1.0)
            for attempt in range(3):
                remaining = self.deadline - time.monotonic()
                wait = max(0, self.last_request.get(host, 0) + delay - time.monotonic())
                if remaining <= wait + 1:
                    raise RuntimeError("HTTP time budget exhausted; remaining sources not checked")
                time.sleep(wait)
                started = time.monotonic()
                response = self.session.get(url, params=params, headers=headers,
                                            timeout=min(45, remaining - wait), allow_redirects=False)
                self.last_request[host] = time.monotonic()
                if response.status_code < 400:
                    break
                # Log source diagnostics, never request headers or credentials.
                snippet = re.sub(r"\s+", " ", response.text[:240])
                token = os.getenv("GITHUB_TOKEN")
                if token:
                    snippet = snippet.replace(token, "***")
                print(f"HTTP {response.status_code} {host}{parsed.path} {time.monotonic()-started:.1f}s "
                      f"retry-after={response.headers.get('Retry-After', '-')} "
                      f"remaining={response.headers.get('X-RateLimit-Remaining', '-')} {snippet}", file=sys.stderr)
                if response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                    response.raise_for_status()
                retry = response.headers.get("Retry-After", str(2 ** attempt))
                try:
                    wait = float(retry)
                except ValueError:
                    raise RuntimeError(f"{host}: non-numeric Retry-After; retry next run")
                if wait < 0 or wait > 30 or time.monotonic() + wait >= self.deadline:
                    raise RuntimeError(f"{host}: retry exceeds daily request budget")
                time.sleep(wait)
            if response.is_redirect:
                if redirect == 3:
                    raise RuntimeError("Too many source redirects")
                url = urljoin(url, response.headers["Location"])
                params = None
                continue
            return response
        raise RuntimeError("Unreachable redirect state")

    def json(self, url, params=None):
        return self._request(url, params).json()

    def text(self, url, params=None):
        response = self._request(url, params)
        response.encoding = response.encoding or "utf-8"
        return response.text

    def page_text(self, url):
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots:
            robots_url = origin + "/robots.txt"
            try:
                content = self.text(robots_url)
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code not in (404, 410):
                    raise
                content = ""
            policy = RobotFileParser(robots_url)
            policy.parse(content.splitlines())
            self.robots[origin] = policy
        policy = self.robots[origin]
        if not policy.can_fetch(USER_AGENT, url):
            raise RuntimeError(f"robots.txt disallows monitoring {origin}{parsed.path}")
        crawl_delay = policy.crawl_delay(USER_AGENT) or 0
        if crawl_delay > 60:
            raise RuntimeError("robots.txt crawl delay exceeds daily request budget")
        if crawl_delay:
            elapsed = time.monotonic() - self.last_request.get(parsed.hostname, 0)
            time.sleep(max(0, crawl_delay - elapsed))
        return self.text(url)


def load_config(path):
    config = read_json(path, None)
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Configuration must have version: 1")
    competitions = config.get("competitions")
    if not isinstance(competitions, list) or not competitions:
        raise ValueError("Configure at least one competition")
    ids, competition_ids = set(), set()
    for competition in competitions:
        cid = competition.get("id")
        if not cid or cid in competition_ids:
            raise ValueError("Competition IDs must be nonempty and unique")
        competition_ids.add(cid)
        if not competition.get("sources"):
            raise ValueError(f"{cid}: no sources configured")
        for source in competition["sources"]:
            sid = source.get("id")
            if not sid or sid in ids:
                raise ValueError("Source IDs must be globally unique and nonempty")
            ids.add(sid)
            if source.get("type") not in ("tig", "github", "page", "arxiv"):
                raise ValueError(f"{sid}: unknown source type")
            source.setdefault("depth", competition.get("depth", "standard"))
            if source["depth"] not in ("light", "standard", "deep"):
                raise ValueError(f"{sid}: invalid depth")
    return config


def safe_text(value):
    return re.sub(r"[\r\n]+", " ", str(value)).replace("<", "&lt;").replace(">", "&gt;")


def source_identity(source):
    # Resource/filter edits need a new baseline; changing work budgets or depth
    # must retain outstanding work and comparison cursors.
    keys = ("type", "repo", "branch", "paths", "api_url", "url", "selector",
            "embedded_json_selectors", "query", "seed_ids")
    identity = {key: source[key] for key in keys if key in source}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def render_report(report):
    today_count = sum(event.get("detected_at", "").startswith(report['date']) for event in report['events'])
    lines = [f"# Competition Tracer — {report['date']}", "",
             f"Letzter Check: {report['checked_at']} · Heute erfasst: {today_count} · Aus früheren Tagen zur Zustellung übernommen: {len(report['events']) - today_count} · Fehler: {report['error_count']}", "",
             "Tägliche Momentaufnahme; keine vollständige Blockhistorie und keine Ausführung eingereichten Codes.", ""]
    if report["error_count"]:
        lines += ["**Unvollständiger Check: Fehler sind keine Entwarnung.**", ""]
    for competition in report["competitions"]:
        lines += ["## " + safe_text(competition["name"]), "", safe_text(competition.get("description", "")), ""]
        for check in competition["checks"]:
            source_label = safe_text(check['source_id'])
            source_url = check.get('url', '')
            if urlparse(source_url).scheme == 'https':
                source_label = f"[{source_label}](<{source_url}>)"
            lines += [f"- **{source_label}** ({check['depth']}): {check['status']}."]
            if check.get("stats"):
                lines += ["  " + safe_text(json.dumps(check["stats"], ensure_ascii=False, sort_keys=True))]
            for warning in check.get("warnings", []):
                lines += ["  Hinweis: " + safe_text(warning)]
            for error in check.get("errors", []):
                lines += ["  Fehler: " + safe_text(error)]
        lines.append("")
        events = [x for x in report["events"] if x["competition_id"] == competition["id"]]
        if not events:
            lines += ["Keine neuen Ereignisse erfasst. Quellenstatus oben beachten.", ""]
        for event in events:
            title = safe_text(event["title"])
            url = event.get("url", "")
            lines += [f"### {title}", "", f"Typ: {safe_text(event['kind'])} · Quelle: {safe_text(event['source_id'])}"]
            lines += ["Erfasst: " + safe_text(event.get("detected_at", ""))]
            if event.get("published"):
                lines += ["Veröffentlicht: " + safe_text(event["published"])]
            if event.get("updated"):
                lines += ["Aktualisiert: " + safe_text(event["updated"])]
            if urlparse(url).scheme in ("https", "http"):
                lines += ["", f"[Originalquelle](<{url}>)"]
            detail = event.get("detail", "")
            if "\n" in detail:
                lines += ["", "```text", detail.replace("```", "` ` `"), "```", ""]
            else:
                lines += ["", safe_text(detail), ""]
            if event.get("metadata"):
                lines += ["```json", json.dumps(event["metadata"], ensure_ascii=False, indent=2).replace("```", "` ` `"), "```", ""]
    return "\n".join(lines) + "\n"


def collect(config, previous, client, now=None, collectors=None):
    if collectors is None:
        from competition_sources import collect_github, collect_page, collect_arxiv
        from competition_tig import collect_tig
        collectors = {"github": collect_github, "page": collect_page, "arxiv": collect_arxiv, "tig": collect_tig}
    now = now or utcnow()
    state = copy.deepcopy(previous)
    state.setdefault("version", 1)
    state.setdefault("sources", {})
    state.setdefault("outbox", {})
    report = {"date": now[:10], "checked_at": now, "competitions": [], "events": [], "error_count": 0}
    for competition in config["competitions"]:
        group = {key: competition.get(key, "") for key in ("id", "name", "description")}
        group["checks"] = []
        for source in competition["sources"]:
            if source.get("enabled", True) is False:
                continue
            sid = source["id"]
            check = {"source_id": sid, "depth": source["depth"], "status": "ok"}
            check["url"] = source.get("url") or ("https://github.com/" + source["repo"] if source.get("repo") else "https://arxiv.org")
            old = state["sources"].get(sid)
            fingerprint = source_identity(source)
            reset = old is not None and old.get("config_hash") != fingerprint
            before = None if old is None or reset else old["data"]
            try:
                result = collectors[source["type"]](source, copy.deepcopy(before), client)
                if not isinstance(result.get("state"), dict) or not isinstance(result.get("events"), list):
                    raise ValueError("Invalid collector response")
                check.update({"stats": result.get("stats", {}), "warnings": list(result.get("warnings", [])), "errors": result.get("errors", [])})
                if before is None:
                    check["status"] = "Baseline angelegt"
                    if reset:
                        check["warnings"].append("Quellenkonfiguration geändert: neue Vergleichsbasis angelegt.")
                if check["errors"]:
                    check["status"] = "unvollständig"
                    report["error_count"] += len(check["errors"])
                events = []
                for event in result["events"]:
                    if not all(event.get(k) for k in ("id", "kind", "title")):
                        raise ValueError("Collector event missing id, kind or title")
                    event = dict(event, source_id=sid, competition_id=competition["id"], detected_at=now)
                    event["key"] = sid + ":" + str(event["id"])
                    events.append(event)
                state["sources"][sid] = {"config_hash": fingerprint, "data": result["state"], "checked_at": now}
                for event in events:
                    report["events"].append(event)
                    state["outbox"][event["key"]] = event
            except Exception as exc:
                # Keep last good source state, continue independent sources.
                check.update({"status": "fehlgeschlagen", "errors": [str(exc)]})
                report["error_count"] += 1
            group["checks"].append(check)
        report["competitions"].append(group)
    state["checked_at"] = now
    return state, report


def write_outputs(values):
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            for key, value in values.items():
                stream.write(f"{key}={safe_text(value)}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="competition_sources.json")
    parser.add_argument("--state", default="seen_competitions.json")
    parser.add_argument("--report-dir", default="reports/competitions")
    parser.add_argument("--alert-file", default="competition-alert.txt")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--acknowledge-delivery", action="store_true")
    args = parser.parse_args(argv)
    state = read_json(args.state, {"version": 1, "sources": {}, "outbox": {}})
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("Unsupported or corrupt competition state")
    if args.acknowledge_delivery:
        pending = state.get("pending_digest")
        if not pending:
            raise ValueError("No pending digest to acknowledge")
        for key in pending["keys"]:
            state["outbox"].pop(key, None)
        state["delivered_day"] = pending["date"]
        del state["pending_digest"]
        if not args.dry_run:
            atomic_json(args.state, state)
        return 0
    config = load_config(args.config)
    state, report = collect(config, state, Client())
    directory = Path(args.report_dir)
    daily = directory / (report["date"] + ".json")
    old_report = read_json(daily, {"events": []})
    events = {event["key"]: event for event in old_report["events"]}
    # Late findings held after yesterday's delivery must appear behind today's
    # digest's full-report link, with their original detection date preserved.
    events.update(state["outbox"])
    events.update({event["key"]: event for event in report["events"]})
    report["events"] = list(events.values())
    known_groups = {group["id"] for group in report["competitions"]}
    for cid in dict.fromkeys(event["competition_id"] for event in report["events"]):
        if cid not in known_groups:
            report["competitions"].append({"id": cid, "name": cid, "description": "Frühere, noch nicht zugestellte Ereignisse einer entfernten Konfiguration.", "checks": []})
    markdown = render_report(report)
    send = state.get("delivered_day") != report["date"]
    subject = f"Competition Tracer {report['date']}: {len(state['outbox'])} Neuerungen, {report['error_count']} Fehler"
    digest = [subject, "", ROOT_URL, ""]
    if report["error_count"]:
        digest += ["Unvollständige Quellenchecks; Details im Bericht.", ""]
    for event in state["outbox"].values():
        digest += [f"[{event['competition_id']}] {safe_text(event['title'])}", event.get("url", ""), ""]
    if not state["outbox"]:
        digest += ["Keine neuen Ereignisse. Baselines und Quellenstatus stehen im Bericht."]
    if not args.dry_run:
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(daily, report)
        (directory / (report["date"] + ".md")).write_text(markdown, encoding="utf-8")
        (directory / "latest.md").write_text(markdown, encoding="utf-8")
        alert = Path(args.alert_file)
        alert.parent.mkdir(parents=True, exist_ok=True)
        alert.write_text("\n".join(digest) + "\n", encoding="utf-8")
        if send:
            state["pending_digest"] = {"date": report["date"], "keys": list(state["outbox"])}
        atomic_json(args.state, state)
    else:
        print(markdown)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(markdown)
    write_outputs({"alert_count": int(send and not args.dry_run), "error_count": report["error_count"], "subject": subject})
    print(f"Competition checks complete: {len(report['events'])} daily events; {report['error_count']} errors; dry_run={args.dry_run}")
    # Actions persists independent successful checks, then explicitly fails its final step.
    return 0 if os.getenv("GITHUB_OUTPUT") else int(report["error_count"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
