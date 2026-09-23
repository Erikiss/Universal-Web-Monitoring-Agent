"""Daily, change-only watch for arXiv:2609.12591 and verified author sources.

The workflow sends the prepared digest through the existing send_alert.py and
only commits the proposed state after SMTP succeeds. No credentials live here.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PAPER_ID = "2609.12591"
ARXIV_URL = f"https://arxiv.org/abs/{PAPER_ID}"
HPI_URL = "https://hpi.de/giese/people/christian-medeiros-adriano.html"
KEYWORDS = re.compile(
    r"2609\.12591|transition[\s_-]*atlas|feature[\s_-]*flow|decoder[\s_-]*cosine|"
    r"sae[\s_-]*atlas", re.I
)
EVENT_TYPES = {
    "PushEvent", "CreateEvent", "DeleteEvent", "ReleaseEvent", "PublicEvent",
    "PullRequestEvent", "PullRequestReviewEvent", "PullRequestReviewCommentEvent",
    "IssuesEvent", "IssueCommentEvent", "CommitCommentEvent", "GollumEvent",
}
LABELS = {
    "paper": "NEUE PAPER-VERSION",
    "github": "AUTOREN-GITHUB",
    "hpi": "HPI-LINKHINWEIS",
}


def compact(value: str) -> str:
    return " ".join(value.split())


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def canonical_url(value: str, base: str = HPI_URL) -> str:
    parts = urlsplit(urljoin(base, value))
    if parts.scheme not in {"https", "http"} or parts.username or parts.password:
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


class Client:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "Transition-Atlas-Watch/1.0 (+https://github.com/Erikiss/Universal-Web-Monitoring-Agent)"
        retries = Retry(total=2, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504),
                        allowed_methods=frozenset({"GET"}), respect_retry_after_header=False)
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def get(self, url: str) -> requests.Response:
        headers = {}
        # Never send the GitHub token to arXiv, HPI or an arbitrary linked site.
        if urlsplit(url).hostname == "api.github.com":
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
            if os.getenv("GITHUB_TOKEN"):
                headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
        response = self.session.get(url, headers=headers, timeout=(10, 35))
        response.raise_for_status()
        return response

    def pages(self, url: str, max_pages: int = 30) -> list[dict]:
        rows = []
        for page in range(1, max_pages + 1):
            sep = "&" if "?" in url else "?"
            response = self.get(f"{url}{sep}per_page=100&page={page}")
            data = response.json()
            if not isinstance(data, list):
                raise ValueError("GitHub returned a non-list response")
            rows.extend(data)
            if len(data) < 100:
                return rows
        raise ValueError(f"GitHub pagination limit ({max_pages} pages) reached; incomplete result discarded")


def parse_arxiv_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    if PAPER_ID not in soup.get_text(" ", strip=True) and PAPER_ID not in html:
        raise ValueError("arXiv response does not identify the watched paper")
    history = soup.select_one(".submission-history")
    versions = re.findall(r"\[v(\d+)\]", history.get_text(" ") if history else "")
    # Exact ID fallback, not arbitrary version strings from the page.
    if not versions:
        versions = re.findall(re.escape(PAPER_ID) + r"v(\d+)\b", html)
    if not versions:
        raise ValueError("No explicit arXiv version found; refusing to assume v1")
    return {"version": max(map(int, versions))}


def parse_arxiv_atom(xml: str) -> dict:
    root = ET.fromstring(xml)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("a:entry", ns):
        identifier = entry.findtext("a:id", default="", namespaces=ns)
        match = re.search(r"/abs/" + re.escape(PAPER_ID) + r"v(\d+)$", identifier)
        if match:
            return {"version": int(match.group(1))}
    raise ValueError("arXiv API did not return an explicit version of the watched paper")


def collect_arxiv(client: Client) -> dict:
    errors = []
    for url, parser in [(ARXIV_URL, parse_arxiv_html),
                        (f"https://export.arxiv.org/api/query?id_list={PAPER_ID}", parse_arxiv_atom)]:
        try:
            return parser(client.get(url).text)
        except (requests.RequestException, ValueError, ET.ParseError) as exc:
            errors.append(type(exc).__name__)
    raise ValueError("arXiv abstract and API checks both failed: " + ", ".join(errors))


def repo_snapshot(rows: list[dict]) -> dict:
    fields = ("full_name", "html_url", "pushed_at", "description", "homepage",
              "default_branch", "archived", "disabled", "fork")
    # Deliberately exclude updated_at, stars, watchers, fork counts, open issues:
    # those can change without an author making a repository/content change.
    return {str(row["id"]): {key: row.get(key) for key in fields} for row in rows}


def release_snapshot(rows: list[dict]) -> dict:
    output = {}
    for row in rows:
        if row.get("draft"):
            continue
        stable = {key: row.get(key) for key in
                  ("id", "name", "tag_name", "target_commitish", "body", "published_at", "prerelease")}
        stable["assets"] = sorted([
            {key: a.get(key) for key in ("id", "name", "size", "updated_at", "digest", "browser_download_url")}
            for a in row.get("assets", [])], key=lambda a: str(a["id"]))
        output[str(row["id"])] = {"name": row.get("name") or row.get("tag_name") or "Release",
                                  "url": row["html_url"], "fingerprint": digest(stable)}
    return output


def event_snapshot(rows: list[dict], previous: dict | None, started_at: str) -> dict:
    known = set((previous or {}).get("seen_ids", []))
    pending = {}
    for row in rows:
        event_id = str(row["id"])
        if (previous is not None and event_id not in known
                and row.get("type") in EVENT_TYPES and row.get("created_at", "") >= started_at):
            payload = row.get("payload", {})
            repo = row.get("repo", {}).get("name", "")
            detail = payload.get("pull_request") or payload.get("issue") or payload.get("comment") or payload.get("release") or {}
            url = detail.get("html_url") or f"https://github.com/{repo}"
            pending[event_id] = {"repo": repo, "type": row["type"], "url": url,
                                 "at": row.get("created_at", "")}
        known.add(event_id)
    # IDs are monotonically assigned numeric strings. Keep a generous dedupe window.
    return {"seen_ids": sorted(known, key=lambda value: int(value))[-5000:], "pending": pending}


def collect_events(client: Client, login: str) -> list[dict]:
    # GitHub's public Events API exposes at most 300 events. Current owned-repo
    # and release snapshots are monitored independently, not inferred from it.
    rows = []
    for page in range(1, 4):
        data = client.get(f"https://api.github.com/users/{login}/events/public?per_page=100&page={page}").json()
        if not isinstance(data, list):
            raise ValueError("GitHub Events returned a non-list response")
        rows.extend(data)
        if len(data) < 100:
            break
    return rows


def is_research_link(url: str, context: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if KEYWORDS.search(url + " " + context):
        return True
    if host in {"github.com", "huggingface.co"}:
        return len(parts.path.strip("/").split("/")) >= 2
    return host.endswith(".github.io") or host in {"zenodo.org", "osf.io", "figshare.com"}


def hpi_snapshot(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    heading = next((h for h in soup.find_all(re.compile("^h[1-3]$"))
                    if "Christian Medeiros Adriano" in h.get_text(" ", strip=True)), None)
    if heading is None:
        raise ValueError("HPI author heading missing; possible error/challenge/redesign, not a content update")
    links, notes = {}, set()
    for element in heading.find_all_next():
        if element.name in {"h1", "h2", "h3"} and compact(element.get_text(" ")) == "Contact":
            break
        if element.name == "a" and element.get("href"):
            url = canonical_url(element["href"])
            if not url or url == canonical_url(HPI_URL):
                continue
            parent = element.find_parent(["li", "p", "tr", "td"])
            context = compact((parent or element).get_text(" ", strip=True))
            label = compact(element.get_text(" ", strip=True))
            if is_research_link(url, context):
                # Keep the shortest useful context when the URL occurs more than once.
                entry = {"label": label, "context": context[:1500]}
                if url not in links or len(entry["context"]) < len(links[url]["context"]):
                    links[url] = entry
        if element.name in {"p", "li", "h1", "h2", "h3", "h4"}:
            text = compact(element.get_text(" ", strip=True))
            if KEYWORDS.search(text):
                notes.add(text[:1500])
    return {"links": links, "notes": sorted(notes)}


def change(kind: str, title: str, url: str, detail: str = "") -> dict:
    return {"kind": kind, "title": compact(title), "url": url, "detail": compact(detail)}


def diff_repos(old: dict | None, new: dict, author: str) -> list[dict]:
    if old is None:
        return []
    output = []
    for repo_id, repo in new.items():
        previous = old.get(repo_id)
        if previous == repo:
            continue
        fields = ", ".join(k for k in repo if previous and repo[k] != previous.get(k))
        description = "Neues öffentliches Repository" if previous is None else "Repository geändert: " + fields
        output.append(change("github", f"{author}: {repo['full_name']}", repo["html_url"], description))
    for repo_id in old.keys() - new.keys():
        repo = old[repo_id]
        output.append(change("github", f"{author}: {repo['full_name']} nicht mehr öffentlich gelistet",
                             repo["html_url"], "Möglicherweise privat, gelöscht oder übertragen; Grund unbekannt."))
    return output


def diff_releases(old: dict | None, new: dict, repo: str) -> list[dict]:
    if old is None:
        return []
    output = []
    for release_id, item in new.items():
        if old.get(release_id) != item:
            output.append(change("github", f"{repo}: Release {item['name']}", item["url"],
                                 "Neues oder verändertes öffentliches Release/Release-Asset."))
    for release_id in old.keys() - new.keys():
        item = old[release_id]
        output.append(change("github", f"{repo}: Release {item['name']} nicht mehr gelistet", item["url"]))
    return output


def diff_hpi(old: dict | None, new: dict) -> list[dict]:
    if old is None:
        return []
    output = []
    for url, item in new["links"].items():
        previous = old.get("links", {}).get(url)
        if previous is None or (KEYWORDS.search(item["context"] + " " + item["label"])
                                and previous != item):
            output.append(change("hpi", "HPI: " + (item["label"] or "neuer Forschungslink"), url, item["context"]))
    for note in set(new["notes"]) - set(old.get("notes", [])):
        if not any(note in item["detail"] or item["detail"] in note for item in output if item["detail"]):
            output.append(change("hpi", "HPI: neuer/veränderter Atlas-/Feature-Flow-Hinweis", HPI_URL, note))
    return output


def collect(client: Client, config: dict, old: dict, now: str) -> tuple[dict, list[dict], list[str]]:
    new = copy.deepcopy(old)
    new.setdefault("schema_version", 1)
    new.setdefault("started_at", now)
    snapshots = new.setdefault("sources", {})
    events, errors = [], []

    def run(key, getter, differ):
        previous = snapshots.get(key)
        try:
            current = getter(previous)
            updates = differ(previous, current)
            snapshots[key] = current
            events.extend(updates)
            return current
        except Exception as exc:
            # Public job logs/reports must never include a token or response body.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            reason = type(exc).__name__ + (f" (HTTP {status})" if status else "")
            errors.append(f"{key}: {reason}; letzter erfolgreicher Stand bleibt erhalten.")
            return None

    def paper_diff(previous, current):
        before = (previous or {}).get("version", config.get("known_arxiv_version", 1))
        after = current["version"]
        if after < before:
            raise ValueError("Stale arXiv response would regress the saved version")
        return [change("paper", f"arXiv v{before} → v{after}", f"{ARXIV_URL}v{after}",
                       "Neue Version des Originalpapers; eine neue Version beweist noch keinen Atlas-Release.")] if after > before else []

    run("arxiv", lambda _: collect_arxiv(client), paper_diff)
    for author in config["github_authors"]:
        login, name = author["login"], author["name"]
        repos = run(f"repos:{login}",
                    lambda _, login=login: repo_snapshot(client.pages(f"https://api.github.com/users/{login}/repos?type=owner&sort=full_name")),
                    lambda a, b, name=name: diff_repos(a, b, name))
        # Reuse the last successful repo inventory if listing fails. Do not erase it.
        inventory = repos if repos is not None else snapshots.get(f"repos:{login}", {})
        for repo in inventory.values():
            full_name = repo["full_name"]
            run(f"releases:{full_name}",
                lambda _, full_name=full_name: release_snapshot(client.pages(f"https://api.github.com/repos/{full_name}/releases")),
                lambda a, b, full_name=full_name: diff_releases(a, b, full_name))

        def event_diff(previous, current, name=name, inventory=inventory, repos=repos):
            owned = {item["full_name"] for item in inventory.values()} if repos is not None else set()
            # Owned-repo pushes/publication are covered by successful snapshots.
            # Keep other author events (e.g. wiki/PR/issue changes), including
            # activity in an owned repo. Never suppress events if listing failed.
            covered_types = {"PushEvent", "CreateEvent", "PublicEvent"}
            return [change("github", f"{name}: {event['type']} in {event['repo']}", event["url"],
                           "Neue öffentliche Repository-Aktivität des verifizierten Autors.")
                    for event in current.get("pending", {}).values()
                    if not (event["repo"] in owned and event["type"] in covered_types)]
        result = run(f"events:{login}",
                     lambda previous, login=login: event_snapshot(collect_events(client, login), previous, new["started_at"]),
                     event_diff)
        if result is not None:
            result.pop("pending", None)
    run("hpi", lambda _: hpi_snapshot(client.get(config.get("hpi_url", HPI_URL)).text), diff_hpi)
    new["checked_at"] = now
    new["last_errors"] = errors
    # Collapse identical source changes, never classify activity as an Atlas release.
    unique = {digest(event): event for event in events}
    return new, list(unique.values()), errors


def render(events: list[dict], errors: list[str], state: dict, config: dict) -> str:
    lines = [f"# Transition-Atlas watch — {state['checked_at']}", "", f"Originalpaper: {ARXIV_URL}",
             f"Neue Hinweise: {len(events)} | fehlgeschlagene Quellenchecks: {len(errors)}",
             "Vollständiger Bericht: https://github.com/Erikiss/Universal-Web-Monitoring-Agent/blob/main/reports/transition_atlas_latest.md", "",
             "GitHub-Aktivität und neue HPI-Links sind Hinweise, kein automatisch bestätigter Atlas-Release.", ""]
    for kind in LABELS:
        selected = [event for event in events if event["kind"] == kind]
        if selected:
            lines.extend([f"## {LABELS[kind]}", ""])
        for event in selected:
            lines.extend([event["title"], event["url"], event["detail"], ""])
    if not events:
        lines.append("Kein neuer Hinweis in den erfolgreich geprüften Quellen. Erster erfolgreicher Abruf einer Quelle setzt nur die Vergleichsbasis.")
    if errors:
        lines.extend(["", "## Unvollständige Prüfung", *errors,
                      "Fehler sind kein Keine-Neuigkeiten-Ergebnis; der Workflow wird als fehlgeschlagen markiert."])
    version = state.get("sources", {}).get("arxiv", {}).get("version")
    lines.extend(["", "## Abdeckung", f"Letzte erfolgreich gelesene Paper-Version: {'v' + str(version) if version else 'noch nicht verfügbar'}",
                  "Verifizierte GitHub-Accounts: " + ", ".join(a["login"] for a in config["github_authors"]),
                  "Noch ohne verifiziertes GitHub-Profil: " + ", ".join(config.get("unresolved_authors", [])),
                  "Nur öffentliche Aktivität. Events API: höchstens 300 jüngste Events; eigene Repositories und Releases zusätzlich als Zustandsvergleich.",
                  "Keine Drittanbieter-Suche, keine Meldung für Sterne/Follower oder bloße HPI-Layoutänderungen.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="transition_atlas_sources.json")
    parser.add_argument("--state", default="seen_transition_atlas.json")
    parser.add_argument("--report", default="reports/transition_atlas_latest.md")
    parser.add_argument("--alert-file", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if config.get("arxiv_id", PAPER_ID) != PAPER_ID:
        raise ValueError("This watcher is scoped to arXiv:" + PAPER_ID)
    state_path = Path(args.state)
    # Corrupt existing state is an error, never silently reset a dedupe baseline.
    old = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if not isinstance(old, dict) or not isinstance(old.get("sources", {}), dict):
        raise ValueError("Invalid state file")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state, events, errors = collect(Client(), config, old, now)
    report = render(events, errors, state, config)
    labels = [LABELS[kind] for kind in LABELS if any(e["kind"] == kind for e in events)]
    subject = "[Web-Monitor] Transition-Atlas | " + " + ".join(labels) + f" ({len(events)})"
    Path(args.alert_file).parent.mkdir(parents=True, exist_ok=True)
    Path(args.alert_file).write_text(report if events else "", encoding="utf-8")
    if not args.dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report, encoding="utf-8")
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"alert_count={len(events)}\nerror_count={len(errors)}\nsubject={subject}\n")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(report)
    print(f"Transition-Atlas: {len(events)} new hints; {len(errors)} source errors; dry_run={args.dry_run}")
    # In Actions, commit successful-source state before the final explicit failure step.
    return 0 if os.getenv("GITHUB_OUTPUT") else (1 if errors else 0)


if __name__ == "__main__":
    raise SystemExit(main())
