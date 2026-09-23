"""Daily Music-JEPA release hints; never execute or download discovered models.

Uses the existing send_alert.py in a separate workflow step. A failed mail step
must prevent committing the new state, so an unsent signal is retried next run.
All external content is untrusted data, never shell commands or Python code.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

PAPER = "2607.22000"
ARXIV = f"https://arxiv.org/abs/{PAPER}"
PROJECT = "https://zzwaang.github.io/music-jepa-demo/"
PROJECT_REPO = "ZZWaang/music-jepa-demo"
AUTHORS = ("ZZWaang", "kunfang98927")
GH = "https://api.github.com"
HF = "https://huggingface.co"
SELF_REPO = "Erikiss/Universal-Web-Monitoring-Agent"
QUERIES = (
    f'"{PAPER}" in:readme fork:false',
    '"Music-JEPA" in:name,description,readme fork:false',
    '"music_jepa" in:name,description,readme fork:false',
    '"Learning a World Model of Sound from Action" in:readme fork:false',
)
RELATED = re.compile(r"2607\.22000|music[-_\s]*jepa|learning a world model of sound from action", re.I)
EXACT_PAPER = re.compile(r"2607\.22000|learning a world model of sound from action", re.I)
CATALOGUE = re.compile(r"awesome|arxiv[-_]?daily|paper[-_]?list|reading[-_]?list|newsletter|tracker", re.I)
WEIGHT_EXT = (".safetensors", ".ckpt", ".pth", ".pt", ".bin", ".onnx", ".h5", ".msgpack")
CODE_EXT = (".py", ".ipynb", ".cu", ".cpp", ".jl")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def links_in(text: str, base: str = PROJECT) -> list[str]:
    """Keep resource links, not arXiv's changing navigation or bibliography UI."""
    soup = BeautifulSoup(text, "html.parser")
    raw = [a["href"] for a in soup.find_all("a", href=True)]
    raw += re.findall(r'https?://[^\s<>"\'\\)\]}]+', text.replace("\\/", "/"))
    result = set()
    for item in raw:
        p = urlsplit(urljoin(base, html.unescape(item).rstrip(".,;")))
        host = (p.hostname or "").lower()
        if p.scheme not in ("http", "https") or p.username or p.password:
            continue
        if host in ("github.com", "huggingface.co", "drive.google.com", "zenodo.org") or p.path.lower().endswith(WEIGHT_EXT):
            result.add(urlunsplit(("https", p.netloc.lower(), p.path.rstrip("/"), p.query, "")))
    return sorted(result)


def resource_id(url: str, host: str) -> str | None:
    p = urlsplit(url)
    parts = p.path.strip("/").split("/")
    if p.hostname != host or len(parts) < 2:
        return None
    if host == "huggingface.co" and parts[0] in ("spaces", "datasets", "papers", "docs", "collections", "blog"):
        return None
    if host == "github.com" and parts[0] in ("features", "topics", "orgs", "users", "settings", "marketplace"):
        return None
    if not all(re.fullmatch(r"[\w.-]+", s) for s in parts[:2]):
        return None
    return "/".join(parts[:2]).removesuffix(".git")


class FetchError(RuntimeError):
    pass


class Client:
    """Bounded retries, pagination and host-scoped GitHub credentials."""
    def __init__(self):
        self.session = requests.Session()

    def get(self, url: str, params=None, missing_ok: bool = False):
        headers = {"User-Agent": "Universal-Web-Monitoring-Agent/Music-JEPA-watch", "Accept": "application/json,text/html,application/atom+xml"}
        if urlsplit(url).hostname == "api.github.com":
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
            token = os.getenv("GITHUB_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        error = "request failed"
        for attempt in range(3):
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=30)
                if r.status_code == 404 and missing_ok:
                    return None
                if r.status_code == 200:
                    if not r.content or len(r.content) > 20_000_000:
                        raise FetchError(f"Empty or oversized response: {url}")
                    return r
                error = f"HTTP {r.status_code}: {url}"
                if r.status_code not in (403, 408, 429, 500, 502, 503, 504):
                    break
            except requests.RequestException as exc:
                # Do not print raw request/response objects or authentication data.
                error = f"{type(exc).__name__}: {url}"
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
        raise FetchError(error)

    def json(self, url: str, params=None, missing_ok: bool = False):
        r = self.get(url, params, missing_ok)
        if r is None:
            return None
        try:
            return r.json()
        except ValueError as exc:
            raise FetchError(f"Invalid JSON: {url}") from exc

    def pages(self, url: str, params=None, items_key: str | None = None) -> list[dict]:
        items = []
        host = urlsplit(url).hostname
        for _ in range(20):
            r = self.get(url, params)
            data = r.json()
            if items_key:
                if not isinstance(data, dict) or data.get("incomplete_results") or data.get("total_count", 0) > 1000:
                    raise FetchError(f"Incomplete search: {url}")
                data = data.get(items_key)
            if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
                raise FetchError(f"Unexpected listing: {url}")
            items.extend(data)
            nxt = r.links.get("next", {}).get("url")
            if not nxt:
                return items
            if urlsplit(nxt).hostname != host or urlsplit(nxt).scheme != "https":
                raise FetchError("Unsafe pagination target")
            url, params = nxt, None
        raise FetchError("Pagination limit reached; not treating partial results as complete")


def parse_arxiv_abs(text: str) -> dict:
    soup = BeautifulSoup(text, "html.parser")
    history = soup.select_one(".submission-history")
    if not history or not RELATED.search(soup.get_text(" ", strip=True)):
        raise FetchError("arXiv abstract page has no valid submission history")
    versions = re.findall(r"\[v(\d+)\]", history.get_text(" ", strip=True))
    if not versions:
        raise FetchError("Cannot extract arXiv version")
    # Only paper metadata, not browser navigation / third-party tools.
    metadata = " ".join(str(x) for x in soup.select(".comments, blockquote.abstract"))
    return {"version": max(map(int, versions)), "links": links_in(metadata, ARXIV)}


def parse_arxiv_atom(text: str) -> dict:
    root = ET.fromstring(text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("a:entry", ns):
        match = re.search(rf"/{re.escape(PAPER)}v(\d+)$", entry.findtext("a:id", "", ns))
        if match:
            return {"version": int(match[1]), "links": links_in(" ".join(entry.itertext()), ARXIV)}
    raise FetchError("arXiv API did not return the requested versioned paper")


def arxiv_snapshot(client: Client) -> dict:
    try:
        return parse_arxiv_abs(client.get(ARXIV).text)
    except (FetchError, ValueError):
        return parse_arxiv_atom(client.get("https://export.arxiv.org/api/query", {"id_list": PAPER}).text)


def new_state() -> dict:
    return {"schema_version": 1, "paper_id": PAPER, "observations": {}, "repo_cache": {}, "errors": {}}


class Watcher:
    def __init__(self, client: Client, state: dict):
        self.client, self.state = client, state
        self.events: list[dict] = []
        self.failed: dict[str, str] = {}
        self.checked: list[str] = []
        self.resource_links: set[str] = set()

    def attempt(self, name: str, fn):
        try:
            result = fn()
            self.checked.append(name)
            return result
        except (FetchError, requests.RequestException, ValueError, KeyError, TypeError, ET.ParseError) as exc:
            message = f"{type(exc).__name__}: {str(exc)[:300]}"
            self.failed[name] = message
            if name not in self.state["errors"]:
                self.events.append({"title": f"Technischer Fehler: {name}", "url": ARXIV,
                                    "detail": message + "\nKein Release-Nachweis; diese Quelle konnte nicht geprueft werden."})
            return None

    def observe(self, key: str, value: dict, title: str, url: str, initial: bool = False):
        old = self.state["observations"].get(key)
        if (old is None and initial) or (old is not None and digest(old) != digest(value)):
            fields = sorted(k for k in value if old is None or old.get(k) != value[k])
            detail = []
            for k in fields:
                before = json.dumps(old.get(k), ensure_ascii=False) if old else "noch nicht erfasst"
                after = json.dumps(value[k], ensure_ascii=False)
                detail.append(f"{k}: {before[:500]} -> {after[:1400]}")
            self.events.append({"title": title, "url": url, "detail": "\n".join(detail)})
        self.state["observations"][key] = value

    def paper(self):
        data = arxiv_snapshot(self.client)
        old = self.state["observations"].get("arxiv")
        if old and data["version"] < old["version"]:
            raise FetchError("Stale arXiv response: refusing to move the version baseline backwards")
        self.resource_links.update(data["links"])
        self.observe("arxiv", data, "arXiv: neue Version oder neue Ressourcenlinks", ARXIV, initial=data["version"] > 1 or bool(data["links"]))

    def project(self):
        text = self.client.get(PROJECT).text
        if not RELATED.search(BeautifulSoup(text, "html.parser").get_text(" ", strip=True)):
            raise FetchError("Project page does not look like Music-JEPA (possible block/error page)")
        links = links_in(text)
        self.resource_links.update(links)
        # Track all HTML, including scripts/styles/media URLs. Ignore only CRLF.
        self.observe("project-page", {"html_sha256": digest(text.replace("\r\n", "\n")), "links": links},
                     "Projektseite geaendert (keine inhaltliche Release-Pruefung)", PROJECT)

    def inspect_repo(self, meta: dict, linked: bool = False):
        name = meta["full_name"]
        if name.lower() in {SELF_REPO.lower(), os.getenv("GITHUB_REPOSITORY", "").lower()}:
            return
        author = name.split("/")[0].lower() in {a.lower() for a in AUTHORS}
        if not author and not linked and CATALOGUE.search(meta.get("name", "")):
            return
        api = f"{GH}/repos/{name}"
        url = f"https://github.com/{name}"
        stamp = digest([meta.get(k) for k in ("pushed_at", "description", "name", "homepage", "default_branch")])
        cached = self.state["repo_cache"].get(name)
        if not cached or cached.get("stamp") != stamp or "exact_paper" not in cached:
            readme = self.client.json(f"{api}/readme", missing_ok=True)
            text = ""
            if readme:
                if readme.get("encoding") != "base64":
                    raise FetchError(f"Unsupported README encoding: {name}")
                text = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")
            context = " ".join(str(meta.get(k) or "") for k in ("name", "description", "homepage")) + " " + text
            cached = {"stamp": stamp, "related": bool(RELATED.search(context)), "exact_paper": bool(EXACT_PAPER.search(context)), "links": links_in(text, url)}
            self.state["repo_cache"][name] = cached
        known = "repo:" + name in self.state["observations"]
        if not (cached["related"] or linked or known or name.lower() == PROJECT_REPO.lower()):
            return
        # Similar names are shared by other papers. Outside author/official
        # links, require this exact paper, not merely a Music-JEPA name match.
        if not (author or linked or known or cached["exact_paper"]):
            return
        self.resource_links.update(cached["links"])
        files = []
        tree_sha = "empty"
        if meta.get("size", 0) > 0:
            tree = self.client.json(f"{api}/git/trees/{quote(meta['default_branch'], safe='')}", {"recursive": "1"})
            if tree.get("truncated") or not isinstance(tree.get("tree"), list):
                raise FetchError(f"Incomplete file tree: {name}")
            files = [x for x in tree["tree"] if x["type"] == "blob"]
            tree_sha = tree["sha"]
        is_demo = name.lower() == PROJECT_REPO.lower()
        if is_demo:
            self.observe("project-source", {"tree_sha": tree_sha}, "Projekt-Repository geaendert (auch Medien/Assets)", url)
        releases = self.client.pages(f"{api}/releases", {"per_page": 100})
        release_info = [{"tag": r["tag_name"], "url": r["html_url"],
                         "assets": sorted((a["name"], a["browser_download_url"], a.get("updated_at")) for a in r.get("assets", []))}
                        for r in releases if not r.get("draft")]
        code = sorted(x["path"] for x in files if x["path"].lower().endswith(CODE_EXT))
        weights = {x["path"]: x["sha"] for x in files if x["path"].lower().endswith(WEIGHT_EXT)}
        data = {"code_files": code, "weight_file_hints": weights,
                "resource_links": cached["links"], "releases": sorted(release_info, key=lambda r: r["url"])}
        # Third-party link lists with no implementation are not release candidates.
        initial = bool(code or weights or release_info) or ((author or linked) and not is_demo)
        label = "Autoren-/Projekt-Repository" if author or linked else "Drittanbieter-Treffer, Zuordnung unbestaetigt"
        self.observe("repo:" + name, data, f"GitHub-Hinweis: {name} ({label})", url, initial=initial)

    def github(self):
        repos: dict[str, dict] = {}
        def collect(items):
            for item in items or []:
                repos[item["full_name"].lower()] = item
        for author in AUTHORS:
            rows = self.attempt(f"GitHub Autor {author}", lambda a=author: self.client.pages(f"{GH}/users/{a}/repos", {"type": "owner", "per_page": 100, "sort": "updated"}))
            collect(rows)
        for query in QUERIES:
            rows = self.attempt(f"GitHub Suche {query}", lambda q=query: self.client.pages(f"{GH}/search/repositories", {"q": q, "per_page": 100}, "items"))
            collect(rows)
            time.sleep(2)  # Leave headroom in the separate Search API rate limit.
        linked = {x for u in self.resource_links if (x := resource_id(u, "github.com"))}
        needed = {PROJECT_REPO} | linked | {k[5:] for k in self.state["observations"] if k.startswith("repo:")}
        for name in sorted(needed):
            if name.lower() not in repos:
                meta = self.attempt(f"GitHub Metadaten {name}", lambda n=name: self.client.json(f"{GH}/repos/{n}"))
                if meta:
                    collect([meta])
        for meta in sorted(repos.values(), key=lambda m: m["full_name"].lower()):
            name = meta["full_name"]
            self.attempt(f"GitHub Repository {name}", lambda m=meta, n=name: self.inspect_repo(m, n.lower() in {x.lower() for x in linked}))

    def inspect_model(self, name: str):
        data = self.client.json(f"{HF}/api/models/{name}")
        if not isinstance(data.get("siblings"), list):
            raise FetchError(f"Hugging Face file list missing: {name}")
        files = sorted(x["rfilename"] for x in data["siblings"] if x["rfilename"].lower().endswith(WEIGHT_EXT))
        snapshot = {"model_id": data.get("id", name), "weight_file_hints": files,
                    "gated": data.get("gated", False), "private": data.get("private", False),
                    "revision_with_weights": data.get("sha") if files else None}
        self.observe("hf:" + name, snapshot, f"Hugging-Face-Hinweis: {name} (Zuordnung/Download nicht bestaetigt)", f"{HF}/{name}", initial=True)

    def huggingface(self):
        models = set()
        searches = [{"search": s} for s in ("music-jepa", "music_jepa", "musicjepa")]
        searches.append({"filter": f"arxiv:{PAPER}"})
        for params in searches:
            rows = self.attempt(f"Hugging Face Suche {next(iter(params.values()))}", lambda p=params: self.client.pages(f"{HF}/api/models", {**p, "limit": 100}))
            for row in rows or []:
                models.add(row["id"])
        models.update(k[3:] for k in self.state["observations"] if k.startswith("hf:"))
        models.update(x for u in self.resource_links if (x := resource_id(u, "huggingface.co")))
        for name in sorted(models):
            self.attempt(f"Hugging Face Modell {name}", lambda n=name: self.inspect_model(n))

    def run(self):
        self.attempt("arXiv", self.paper)
        self.attempt("Projektseite", self.project)
        self.attempt("GitHub discovery", self.github)
        self.attempt("Hugging Face discovery", self.huggingface)
        # Retain a previous outage until that particular source succeeds.
        errors = dict(self.state["errors"])
        for name in self.checked:
            errors.pop(name, None)
        errors.update(self.failed)
        self.state["errors"] = errors
        return self.events


def render(watcher: Watcher, now: str) -> str:
    lines = [f"# Music-JEPA watch — {now}", "", f"Paper: {ARXIV}", f"Projektseite: {PROJECT}", "",
             f"Neue Signale: {len(watcher.events)} | Fehler in diesem Lauf: {len(watcher.failed)}", "",
             "Hinweise sind keine Bestaetigung eines offiziellen, vollstaendigen Releases.",
             "Code und Gewichte werden nicht ausgefuehrt/heruntergeladen; Gating wird ausgewiesen.", ""]
    for event in watcher.events:
        lines.extend([f"## {event['title']}", event["url"], event["detail"], ""])
    if not watcher.events:
        lines.append("Keine neuen Signale bei den erfolgreich geprueften Quellen (Erstabrufe setzen eine Baseline).\n")
    lines += ["## Quellenstatus", *[f"OK: {x}" for x in watcher.checked],
              *[f"FEHLER: {k}: {v}" for k, v in watcher.failed.items()], ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, default=Path("seen_music_jepa.json"))
    parser.add_argument("--report-file", type=Path, default=Path("reports/music_jepa_latest.md"))
    parser.add_argument("--alert-file", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Do not modify state or report files")
    args = parser.parse_args(argv)
    state = json.loads(args.state_file.read_text()) if args.state_file.exists() else new_state()
    if state.get("schema_version") != 1 or state.get("paper_id") != PAPER:
        raise ValueError("State schema/paper mismatch; refusing to reset baseline silently")
    for field in ("observations", "repo_cache", "errors"):
        if not isinstance(state.get(field), dict):
            raise ValueError(f"Invalid state field: {field}")
    watcher = Watcher(Client(), state)
    watcher.run()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = render(watcher, now)
    print(report)
    if args.alert_file:
        if watcher.events:
            args.alert_file.write_text(report, encoding="utf-8")
        else:
            args.alert_file.unlink(missing_ok=True)
    if not args.dry_run:
        state["last_checked"] = now
        args.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(args.state_file)
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        args.report_file.write_text(report, encoding="utf-8")
    for env, content in (("GITHUB_OUTPUT", f"new_count={len(watcher.events)}\nerror_count={len(watcher.failed)}\n"), ("GITHUB_STEP_SUMMARY", report)):
        if os.getenv(env):
            with open(os.environ[env], "a", encoding="utf-8") as out:
                out.write(content)
    # Workflow reports partial failures AFTER sending signals and persisting
    # successful checks. Fatal exceptions still fail this process immediately.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
