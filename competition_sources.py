"""Read-only, independently reusable collectors for Competition Tracer.

Collectors return a candidate state only after a complete successful observation.
The caller must retain the old state on CollectorError. No downloaded code runs.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin, urlparse
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

UTC = timezone.utc
ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
PAPER_ID = re.compile(r"^(\d{4}\.\d{4,5}|[A-Za-z-]+(?:\.[A-Za-z]+)?/\d{7})(?:v(\d+))?$")


class CollectorError(RuntimeError):
    """An incomplete or invalid observation that must not advance a cursor."""


def utc_now():
    return datetime.now(UTC)


def _iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _date(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("missing timezone")
        return result.astimezone(UTC)
    except (AttributeError, TypeError, ValueError) as exc:
        raise CollectorError(f"Invalid timestamp: {value!r}") from exc


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _limit(source, name, default, maximum):
    value = source.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise CollectorError(f"{name} must be an integer between 1 and {maximum}")
    return value


def _result(state, events, stats, warnings=None):
    return {"state": state, "events": events, "stats": stats, "warnings": warnings or []}


def _static_diff(before, after, max_chars):
    patch = "\n".join(difflib.unified_diff(
        before.splitlines(), after.splitlines(), fromfile="previous", tofile="current", lineterm="", n=3))
    if len(patch) > max_chars:
        return patch[:max_chars] + "\n[Diff excerpt truncated.]", True
    return patch, False


def _github_blob(client, api, sha, max_bytes):
    data = client.json(f"{api}/git/blobs/{quote(sha, safe='')}")
    if not isinstance(data, dict) or data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
        raise CollectorError("GitHub blob response is not base64 text")
    if data.get("size", 0) > max_bytes:
        return None
    try:
        raw = base64.b64decode("".join(data["content"].split()), validate=True)
    except ValueError as exc:
        raise CollectorError("Invalid GitHub blob encoding") from exc
    if len(raw) > max_bytes or b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def collect_github(source, previous, client):
    """Compare matching blob SHAs at one pinned commit, with bounded static diffs.

    light/standard detect the same complete set of files; standard adds sizes.
    deep also reads up to max_diff_files changes, without executing any content.
    A truncated tree is a failure, never an empty/complete observation.
    """
    repo = source.get("repo", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise CollectorError("GitHub source requires an owner/repository")
    depth = source.get("depth", "standard")
    if depth not in ("light", "standard", "deep"):
        raise CollectorError(f"Unknown GitHub depth: {depth}")
    branch = source.get("branch", "main")
    paths = source.get("paths", [])
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
        raise CollectorError("GitHub paths must be a list of prefix strings")
    prefixes = [p.lstrip("/") for p in paths]
    api = f"https://api.github.com/repos/{repo}"
    now = utc_now()
    commit = client.json(f"{api}/commits/{quote(branch, safe='')}")
    try:
        sha = commit["sha"]
        tree_sha = commit["commit"]["tree"]["sha"]
        if not isinstance(sha, str) or not isinstance(tree_sha, str):
            raise KeyError("sha")
    except (TypeError, KeyError) as exc:
        raise CollectorError("Invalid GitHub commit response") from exc
    tree = client.json(f"{api}/git/trees/{quote(tree_sha, safe='')}", params={"recursive": "1"})
    if not isinstance(tree, dict) or not isinstance(tree.get("tree"), list) or tree.get("truncated") is not False:
        raise CollectorError("GitHub tree is invalid or truncated; source coverage is incomplete")
    files = {}
    for item in tree["tree"]:
        if not isinstance(item, dict):
            raise CollectorError("Malformed GitHub tree entry")
        if item.get("type") != "blob":
            continue
        path, blob = item.get("path"), item.get("sha")
        if not isinstance(path, str) or not isinstance(blob, str):
            raise CollectorError("GitHub blob is missing a path or SHA")
        if not prefixes or any(path.startswith(p) for p in prefixes):
            files[path] = {"sha": blob, "size": item.get("size", 0)}
    state = {"commit": sha, "branch": branch, "files": files, "last_success": _iso(now)}
    stats = {"files": len(files), "baseline": previous is None, "depth": depth, "commit": sha}
    if previous is None:
        return _result(state, [], stats)
    old_files = previous.get("files")
    if not isinstance(old_files, dict):
        raise CollectorError("Previous GitHub state lacks a valid file snapshot")
    events, warnings = [], []
    max_files = _limit(source, "max_diff_files", 10, 100)
    max_bytes = _limit(source, "max_file_bytes", 65536, 1048576)
    max_chars = _limit(source, "max_diff_chars", 4000, 50000)
    inspected = 0
    skipped = 0
    for path in sorted(set(old_files) | set(files)):
        old, new = old_files.get(path), files.get(path)
        if old and new and old["sha"] == new["sha"]:
            continue
        action = "added" if old is None else "deleted" if new is None else "changed"
        detail = f"File {action}."
        metadata = {"path": path, "action": action, "commit": sha,
                    "old_sha": old["sha"] if old else None, "new_sha": new["sha"] if new else None}
        if depth != "light":
            metadata.update(old_size=old.get("size") if old else None, new_size=new.get("size") if new else None)
        if depth == "deep":
            if inspected < max_files:
                inspected += 1
                before = (None if old.get("size", 0) > max_bytes else _github_blob(client, api, old["sha"], max_bytes)) if old else ""
                after = (None if new.get("size", 0) > max_bytes else _github_blob(client, api, new["sha"], max_bytes)) if new else ""
                if before is not None and after is not None:
                    diff, truncated = _static_diff(before, after, max_chars)
                    metadata.update(diff=diff, diff_truncated=truncated)
                    detail += " Static source diff inspected."
                    if truncated:
                        detail += " Diff excerpt truncated."
                else:
                    detail += " Diff omitted: binary, non-UTF-8, or file-size limit."
                    metadata["diff_omitted"] = "binary_or_size"
            else:
                skipped += 1
                detail += " Diff omitted: per-source inspection limit reached."
                metadata["diff_omitted"] = "inspection_limit"
        target_commit = previous.get("commit", sha) if new is None else sha
        url = f"https://github.com/{repo}/blob/{quote(target_commit, safe='')}/{quote(path, safe='/')}"
        event_id = f"github:{repo}:{path}:{metadata['old_sha']}:{metadata['new_sha']}"
        events.append({"id": event_id, "kind": "code_" + action, "title": f"{repo}: {path} {action}",
                       "url": url, "detail": detail, "metadata": metadata})
    if skipped:
        warnings.append(f"All changed file SHAs recorded; {skipped} static diffs omitted by max_diff_files={max_files}.")
    stats.update(changes=len(events), diffs_attempted=inspected, diffs_omitted_by_limit=skipped)
    return _result(state, events, stats, warnings)


def collect_page(source, previous, client):
    """Observe visible server HTML and optionally explicitly selected inline JSON.

    JavaScript is not evaluated. External leaderboard JSON requires its own source;
    an unchanged HTML shell cannot establish an unchanged JavaScript leaderboard.
    """
    url = source.get("url", "")
    if urlparse(url).scheme not in ("http", "https"):
        raise CollectorError("Page source requires an HTTP(S) URL")
    soup = BeautifulSoup(getattr(client, "page_text", client.text)(url), "html.parser")
    selector = source.get("selector")
    if selector:
        try:
            nodes = soup.select(selector)
        except Exception as exc:
            raise CollectorError(f"Invalid page selector: {selector}") from exc
        if not nodes:
            raise CollectorError(f"Page selector matched nothing: {selector}")
    else:
        nodes = [soup.find("main") or soup.find("article") or soup.body or soup]
    embedded = []
    for json_selector in source.get("embedded_json_selectors", []):
        selected = soup.select(json_selector)
        if not selected:
            raise CollectorError(f"Embedded JSON selector matched nothing: {json_selector}")
        for node in selected:
            try:
                embedded.append(json.dumps(json.loads(node.get_text()), sort_keys=True, ensure_ascii=False))
            except (ValueError, TypeError) as exc:
                raise CollectorError("Selected embedded data is not valid JSON") from exc
    for node in soup.select("script, style, nav, footer, header, noscript, [role='navigation']"):
        node.decompose()
    normalized = "\n".join(" ".join(node.stripped_strings) for node in nodes if node.name)
    links = []
    base_tag = soup.find("base", href=True)
    base_url = urljoin(url, base_tag["href"]) if base_tag else url
    for node in nodes:
        if not node.name:
            continue
        anchors = ([node] if node.name == "a" and node.has_attr("href") else []) + list(node.select("a[href]"))
        for anchor in anchors:
            target = urljoin(base_url, anchor["href"].strip())
            if urlparse(target).scheme in ("http", "https"):
                links.append({"label": anchor.get_text(" ", strip=True), "url": target})
    normalized = normalized.strip() + ("\n" + "\n".join(embedded) if embedded else "")
    if len(normalized.strip()) < source.get("min_text_chars", 20):
        raise CollectorError("Page contains too little observable content; refusing to baseline an empty shell")
    title = soup.title.get_text(" ", strip=True) if soup.title else source.get("name", url)
    if title.strip().lower() in ("just a moment...", "access denied", "attention required! | cloudflare"):
        raise CollectorError("Page appears to be an access/challenge response")
    if links:
        normalized += "\nLinked resources: " + json.dumps(links, ensure_ascii=False, sort_keys=True)
    fingerprint = _digest(normalized)
    state = {"sha256": fingerprint, "text": normalized, "last_success": _iso(utc_now())}
    events = []
    if previous is not None and fingerprint != previous.get("sha256"):
        diff, truncated = _static_diff(previous.get("text", ""), normalized, _limit(source, "max_diff_chars", 4000, 50000))
        events.append({"id": f"page:{url}:{previous.get('sha256')}:{fingerprint}", "kind": "page_changed",
                       "title": f"Page changed: {title}", "url": url,
                       "detail": "Server-rendered content changed." + (" Diff excerpt truncated." if truncated else ""),
                       "metadata": {"diff": diff, "diff_truncated": truncated, "javascript_evaluated": False}})
    return _result(state, events, {"baseline": previous is None, "characters": len(normalized),
                                  "javascript_evaluated": False, "embedded_json_blocks": len(embedded),
                                  "linked_resources": len(links)})


def _paper_id(value):
    if value.startswith(("https://", "http://")):
        parsed = urlparse(value)
        if parsed.hostname not in ("arxiv.org", "www.arxiv.org") or not parsed.path.startswith("/abs/"):
            raise CollectorError(f"Invalid arXiv paper URL: {value}")
        value = parsed.path[len("/abs/"):]
    match = PAPER_ID.fullmatch(value)
    if not match:
        raise CollectorError(f"Invalid arXiv paper ID: {value}")
    return match.group(1), int(match.group(2) or 1)


def parse_arxiv_atom(xml, require_total=False):
    """Parse complete Atom responses, treating arXiv API error feeds as errors."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise CollectorError("Invalid arXiv Atom XML") from exc
    if root.tag != ATOM + "feed":
        raise CollectorError("arXiv response is not an Atom feed")
    total_text = root.findtext(OPENSEARCH + "totalResults")
    try:
        total = int(total_text) if total_text is not None else None
    except ValueError as exc:
        raise CollectorError("Invalid arXiv totalResults") from exc
    entries = []
    for entry in root.findall(ATOM + "entry"):
        identifier = entry.findtext(ATOM + "id", "").strip()
        if "/api/errors" in identifier or entry.findtext(ATOM + "title", "").lower() == "error":
            raise CollectorError("arXiv API error: " + entry.findtext(ATOM + "summary", "unknown error"))
        base, version = _paper_id(identifier)
        title = " ".join(entry.findtext(ATOM + "title", "").split())
        published = entry.findtext(ATOM + "published", "")
        updated = entry.findtext(ATOM + "updated", "")
        if not title:
            raise CollectorError("arXiv entry is missing its title")
        _date(published)
        _date(updated)
        authors = [" ".join(a.findtext(ATOM + "name", "").split()) for a in entry.findall(ATOM + "author")]
        entries.append({"paper_id": base, "version": version, "title": title,
                        "published": published, "updated": updated,
                        "summary": " ".join(entry.findtext(ATOM + "summary", "").split()), "authors": authors,
                        "url": f"https://arxiv.org/abs/{base}v{version}"})
    if require_total and total is None:
        raise CollectorError("arXiv discovery response is missing totalResults")
    if total is not None and (total < 0 or total < len(entries)):
        raise CollectorError("Inconsistent arXiv result count")
    return entries, total


def collect_arxiv(source, previous, client):
    """Discover recent papers/updates with overlap and outage catchup.

    Initial runs notify recent *submissions*, not every old paper revised recently.
    Later runs distinguish new submissions, newly discovered older papers, and
    revisions observed against a known previous version.
    Queries sort by update time and paginate until the overlap boundary is covered.
    max_pages overflow raises, preserving the old successful cursor for a retry.
    """
    now = utc_now()
    lookback = _limit(source, "initial_lookback_days", 7, 3650)
    overlap = _limit(source, "overlap_days", 2, 365)
    cutoff = (_date(previous["last_success"]) - timedelta(days=overlap)
              if previous else now - timedelta(days=lookback))
    page_size = _limit(source, "page_size", 100, 200)
    max_pages = _limit(source, "max_pages", 10, 100)
    query = source.get("query", "")
    seeds = source.get("seed_ids", [])
    if not query and not seeds:
        raise CollectorError("arXiv source requires query or seed_ids")
    gathered, pages = {}, 0
    if query:
        previous_oldest = None
        for page in range(max_pages):
            params = {"search_query": query, "sortBy": "lastUpdatedDate", "sortOrder": "descending",
                      "start": page * page_size, "max_results": page_size}
            entries, total = parse_arxiv_atom(client.text(ARXIV_API, params=params), require_total=True)
            pages += 1
            if not entries:
                if total is not None and page * page_size < total:
                    raise CollectorError("arXiv returned an empty page before totalResults was exhausted")
                break
            dates = [_date(e["updated"]) for e in entries]
            if dates != sorted(dates, reverse=True):
                raise CollectorError("arXiv response is not sorted by lastUpdatedDate")
            if previous_oldest is not None and max(dates) > previous_oldest:
                raise CollectorError("arXiv pagination changed during collection; retry to avoid gaps")
            previous_oldest = min(dates)
            for paper in entries:
                if _date(paper["updated"]) >= cutoff:
                    gathered[paper["paper_id"]] = paper
            if (total is not None and len(entries) < page_size
                    and page * page_size + len(entries) < total and min(dates) >= cutoff):
                raise CollectorError("arXiv returned a partial page before totalResults was exhausted")
            if min(dates) < cutoff or len(entries) < page_size or (total is not None and (page + 1) * page_size >= total):
                break
        else:
            raise CollectorError(f"arXiv max_pages={max_pages} reached before the overlap boundary; increase the limit")
    if not isinstance(seeds, list) or len(seeds) > 100:
        raise CollectorError("arXiv seed_ids must be a list of at most 100 IDs")
    wanted = {_paper_id(seed)[0] for seed in seeds}
    if wanted:
        entries, _ = parse_arxiv_atom(client.text(ARXIV_API, params={"id_list": ",".join(sorted(wanted)), "max_results": len(wanted)}))
        found = {e["paper_id"] for e in entries}
        if found != wanted:
            raise CollectorError("arXiv did not return every requested seed paper")
        gathered.update({e["paper_id"]: e for e in entries})
    seen = dict(previous.get("seen", {})) if previous else {}
    events = []
    for base, paper in sorted(gathered.items(), key=lambda item: (item[1]["updated"], item[0])):
        old = seen.get(base)
        if old and paper["version"] < old["version"]:
            raise CollectorError(f"arXiv returned an older version of {base}; refusing to regress the cursor")
        if old and (paper["version"] == old["version"] and _date(paper["updated"]) < _date(old["updated"])):
            raise CollectorError(f"arXiv returned stale update metadata for {base}")
        changed = not old or paper["version"] != old["version"] or paper["updated"] != old["updated"]
        recent_submission = _date(paper["published"]) >= cutoff
        should_notify = changed and (previous is not None or recent_submission)
        if should_notify:
            new_submission = old is None and recent_submission
            kind = "paper_published" if new_submission else "paper_updated" if old else "paper_discovered"
            detail = paper["summary"]
            metadata = {"paper_id": base, "version": paper["version"], "updated": paper["updated"],
                        "authors": paper["authors"], "previous_version": old["version"] if old else None}
            events.append({"id": f"arxiv:{base}:v{paper['version']}:{paper['updated']}", "kind": kind,
                           "title": paper["title"], "url": paper["url"], "detail": detail,
                           "published": paper["published"], "updated": paper["updated"], "metadata": metadata})
        seen[base] = {"version": paper["version"], "updated": paper["updated"]}
    return _result({"seen": seen, "last_success": _iso(now)}, events,
                   {"baseline": previous is None, "pages": pages, "papers_observed": len(gathered),
                    "cutoff": _iso(cutoff), "seed_papers": len(wanted)})
