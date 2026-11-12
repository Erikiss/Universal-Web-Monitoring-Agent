"""Read-only, incremental monitoring of TIG's public algorithm submissions.

The public source branches are distinct from blockchain blocks.  API data is
pinned to one block; source data is pinned to each discovered Git commit.  This
module never imports, compiles, or executes a participant's program.
"""

from __future__ import annotations

import base64
import copy
import difflib
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import quote


DEFAULT_REPO = "tig-foundation/tig-monorepo"
DEFAULT_API = "https://mainnet-api.tig.foundation"
SOURCE_SUFFIXES = (".rs", ".cu", ".cuh", ".c", ".cpp", ".h", ".md", ".txt", ".toml")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def _mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"TIG: {label} must be an object")
    return value


def _records(payload, key):
    data = payload.get(key)
    if not isinstance(data, list):
        raise ValueError(f"TIG: required {key!r} array missing or invalid")
    return data


def _positive(source, name, default):
    value = source.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"TIG: {name} must be a positive integer")
    return value


def _fraction(value):
    """TIG PreciseNumber serializes fixed-point integers as strings (18 dp)."""
    if value is None:
        return None
    try:
        number = Decimal(str(value))
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
            number /= Decimal(10**18)
        elif isinstance(value, int) and abs(value) > 1:
            number /= Decimal(10**18)
        return float(number) if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _stage(record, round_number, compiled=None):
    state = record["state"]
    if state.get("banned"):
        return "banned"
    if compiled is False:
        return "compile_failed"
    if state.get("round_merged") is not None:
        return "merged"
    if state.get("round_active") is not None and state["round_active"] <= round_number:
        return "active"
    if state.get("round_pushed") is not None and state["round_pushed"] <= round_number:
        return "public"
    return "submitted"


def _event(kind, key, title, url, detail, metadata):
    identity = {name: value for name, value in metadata.items() if name not in {"block_id", "height"}}
    return {"id": f"tig:{kind}:{key}:{_digest(identity)}", "kind": kind,
            "title": title, "url": url, "detail": detail, "metadata": metadata}


def _branches(client, repo, max_pages):
    """A partial inventory must never be mistaken for branch deletions."""
    result = {}
    for page in range(1, max_pages + 1):
        data = client.json(f"https://api.github.com/repos/{repo}/branches",
                           params={"per_page": 100, "page": page})
        if not isinstance(data, list):
            raise ValueError("TIG: GitHub branches response is not an array")
        for item in data:
            item = _mapping(item, "branch")
            name = item.get("name")
            commit = _mapping(item.get("commit"), "branch.commit")
            sha = commit.get("sha")
            if not isinstance(name, str) or not isinstance(sha, str) or not sha:
                raise ValueError("TIG: branch name/SHA missing")
            result[name] = sha
        if len(data) < 100:
            return result
    raise ValueError(f"TIG: branch inventory exceeds max_branch_pages={max_pages}; no complete snapshot")


def _inspect_branch(client, repo, branch, head, old, limits, deep):
    challenge, name = branch.split("/", 1)
    prefix = f"tig-algorithms/src/{challenge}/{name}/"
    # Legacy one-file submissions and Advance evidence are included as well.
    exact = {f"tig-algorithms/src/{challenge}/{name}.rs",
             f"tig-algorithms/src/{challenge}/{name}.cu",
             f"tig-algorithms/advances/{challenge}/{name}.md"}
    data = _mapping(client.json(f"https://api.github.com/repos/{repo}/git/trees/{head}",
                                params={"recursive": 1}), "Git tree")
    if data.get("truncated") is not False:
        raise ValueError("GitHub returned a truncated tree; source snapshot not advanced")
    tree = _records(data, "tree")
    selected = []
    for item in tree:
        item = _mapping(item, "tree entry")
        path = item.get("path", "")
        if (isinstance(path, str) and item.get("type") == "blob"
                and (path.startswith(prefix) or path in exact)
                and path.lower().endswith(SOURCE_SUFFIXES)):
            if not isinstance(item.get("sha"), str):
                raise ValueError(f"Missing source blob SHA: {path}")
            selected.append(item)
    if not selected:
        raise ValueError("No algorithm source or Advance evidence at the expected branch path")
    if len(selected) > limits["max_source_files"]:
        raise ValueError(f"Submission has {len(selected)} source files, exceeding max_source_files; snapshot not advanced")
    files = {item["path"]: item["sha"] for item in selected}
    old_files = (old or {}).get("files", {})
    changed = sorted(path for path, sha in files.items() if old_files.get(path) != sha)
    removed = sorted(set(old_files) - set(files))
    excerpts = copy.deepcopy((old or {}).get("excerpts", {}))
    excerpts = {path: value for path, value in excerpts.items() if path in files}
    notes = []
    diffs = []
    if deep:
        ordered = sorted((item for item in selected if item["path"] in changed),
                         key=lambda item: (not item["path"].lower().endswith(".md"), item["path"]))
        chosen = ordered[:limits["max_content_files"]]
        if len(ordered) > len(chosen):
            notes.append(f"Text analysis limited to {len(chosen)}/{len(ordered)} changed files; all source hashes recorded")
        for item in chosen:
            path, sha = item["path"], item["sha"]
            if not isinstance(item.get("size"), int) or item["size"] > limits["max_file_bytes"]:
                excerpts.pop(path, None)
                notes.append(f"Text omitted for oversized/unknown-size file: {path}")
                continue
            blob = _mapping(client.json(f"https://api.github.com/repos/{repo}/git/blobs/{sha}"), "source blob")
            if blob.get("encoding") != "base64" or not isinstance(blob.get("content"), str):
                raise ValueError(f"Unsupported source blob encoding: {path}")
            raw = base64.b64decode(blob["content"], validate=False)
            if len(raw) > limits["max_file_bytes"]:
                raise ValueError(f"Blob exceeded declared size limit: {path}")
            text = raw.decode("utf-8", errors="replace")
            excerpt = text[:limits["max_excerpt_chars"]]
            if len(text) > len(excerpt):
                notes.append(f"Stored excerpt truncated: {path}")
            old_excerpt = excerpts.get(path)
            if old_excerpt is not None and old_excerpt != excerpt:
                lines = list(difflib.unified_diff(old_excerpt.splitlines(), excerpt.splitlines(),
                                                 fromfile=f"previous/{path}", tofile=f"current/{path}", lineterm=""))
                diffs.extend(lines)
            excerpts[path] = excerpt
        # A changed hash must not retain an excerpt from a previous file version.
        chosen_paths = {item["path"] for item in chosen}
        for path in changed:
            if path not in chosen_paths:
                excerpts.pop(path, None)
    else:
        excerpts = {}
    # Keep state bounded even after different files were sampled over many runs.
    keep = sorted(excerpts, key=lambda path: (not path.lower().endswith(".md"), path))[:limits["max_content_files"]]
    excerpts = {path: excerpts[path] for path in keep}
    readme = next((text for path, text in excerpts.items() if path.lower().endswith(".md")), "")
    readme_lines = [line.strip() for line in readme.splitlines()
                    if re.search(r"(?i)(challenge name|algorithm name|submitter|creator|unique algorithm|\bUAI\b)", line)]
    summary = "\n".join(readme_lines[:8])[:1500]
    result = {"head": head, "files": files, "excerpts": excerpts,
              "readme_summary": summary, "analysis_notes": notes}
    detail = f"{len(changed)} added/changed source files; {len(removed)} removed; {len(files)} source files total."
    if summary:
        detail += "\nREADME metadata (author supplied):\n" + summary
    if diffs:
        detail += "\nBounded text-excerpt diff:\n" + "\n".join(diffs[:limits["max_diff_lines"]])
        if len(diffs) > limits["max_diff_lines"]:
            notes.append("Diff truncated at max_diff_lines")
    if notes:
        detail += "\n" + "\n".join(notes)
    return result, changed, removed, detail


def collect_tig(source: dict, previous: dict | None, client) -> dict:
    """Collect one consistent metadata snapshot and bounded public-source updates.

    Required API/schema failures raise. Individual source failures retain the
    previous successful source snapshot and remain in the pending backlog.
    """
    repo = source.get("repo", DEFAULT_REPO)
    api = source.get("api_url", DEFAULT_API).rstrip("/")
    depth = source.get("depth", "deep")
    if depth not in {"light", "metadata", "standard", "deep"}:
        raise ValueError("TIG: depth must be light, metadata, standard or deep")
    limits = {key: _positive(source, key, default) for key, default in {
        "max_branch_pages": 50, "max_source_checks": 30, "max_source_files": 100,
        "max_file_bytes": 60000, "max_content_files": 4, "max_diff_lines": 60,
        "max_excerpt_chars": 4000}.items()}
    threshold = float(source.get("adoption_delta", 0.05))
    if not 0 < threshold <= 1:
        raise ValueError("TIG: adoption_delta must be in (0, 1]")
    previous = previous or {}
    baseline = not previous.get("initialized", False)
    state = copy.deepcopy(previous)
    events, errors = [], []
    block = _mapping(_mapping(client.json(f"{api}/get-block"), "block response").get("block"), "block")
    details = _mapping(block.get("details"), "block.details")
    if not isinstance(block.get("id"), str) or not isinstance(details.get("round"), int) or not isinstance(details.get("height"), int):
        raise ValueError("TIG: block id, round or height invalid")
    old_block = previous.get("block", {})
    if old_block.get("height", -1) > details["height"]:
        raise ValueError("TIG: latest block regressed; snapshot not advanced")
    params = {"block_id": block["id"]}
    challenge_data = _mapping(client.json(f"{api}/get-challenges", params=params), "challenges response")
    challenges = {}
    for record in _records(challenge_data, "challenges"):
        record = _mapping(record, "challenge")
        config = _mapping(record.get("config"), "challenge.config")
        if not isinstance(record.get("id"), str) or not isinstance(config.get("name"), str):
            raise ValueError("TIG: challenge id/name invalid")
        challenges[record["id"]] = config["name"]
    if not challenges:
        raise ValueError("TIG: empty challenge inventory")
    payload = _mapping(client.json(f"{api}/get-algorithms", params=params), "algorithms response")
    binaries = {}
    for binary in _records(payload, "binarys"):
        binary = _mapping(binary, "binary")
        bd = _mapping(binary.get("details"), "binary.details")
        if not isinstance(binary.get("algorithm_id"), str) or not isinstance(bd.get("compile_success"), bool):
            raise ValueError("TIG: binary id/compile_success invalid")
        binaries[binary["algorithm_id"]] = bd["compile_success"]
    records = [("code", item) for item in _records(payload, "codes")]
    if "advances" in payload:
        records.extend(("advance", item) for item in _records(payload, "advances"))
    snapshots = {}
    for record_kind, record in records:
        record = _mapping(record, record_kind)
        rd = _mapping(record.get("details"), f"{record_kind}.details")
        rs = _mapping(record.get("state"), f"{record_kind}.state")
        if any(not isinstance(value, str) or not value for value in (record.get("id"), rd.get("name"), rd.get("challenge_id"))):
            raise ValueError(f"TIG: invalid {record_kind} identity")
        for key in ("round_pushed", "round_active", "round_merged", "round_submitted", "block_confirmed"):
            if rs.get(key) is not None and (isinstance(rs[key], bool) or not isinstance(rs[key], int)):
                raise ValueError(f"TIG: invalid {record_kind} state.{key}")
        if rd["challenge_id"] not in challenges:
            raise ValueError(f"TIG: unknown challenge {rd['challenge_id']}")
        name = rd["name"]
        branch = f"{challenges[rd['challenge_id']]}/{name}"
        stage = _stage(record, details["round"], binaries.get(record["id"]))
        bd = record.get("block_data") or {}
        _mapping(bd, f"{record_kind}.block_data")
        key = f"{record_kind}:{record['id']}"
        snapshot = {"id": record["id"], "record_kind": record_kind, "name": name,
                    "branch": branch, "challenge": challenges[rd["challenge_id"]],
                    "details": rd, "state": rs, "stage": stage,
                    "compile_success": binaries.get(record["id"]),
                    "adoption": _fraction(bd.get("adoption")),
                    "merge_points": bd.get("merge_points"), "reward": bd.get("reward")}
        snapshots[key] = snapshot
        old = previous.get("records", {}).get(key)
        public = rs.get("round_pushed") is not None and rs["round_pushed"] <= details["round"]
        url = (f"https://github.com/{repo}/tree/{quote(branch, safe='/')}/tig-algorithms/"
               if public else "https://docs.tig.foundation/innovators/code-submission")
        changes = []
        if old is None:
            changes.append("Newly observed submission")
        else:
            if old.get("stage") != stage:
                changes.append(f"Status: {old.get('stage')} → {stage}")
            if old.get("details") != rd or old.get("state") != rs:
                changes.append("Submission metadata/state changed")
            if old.get("compile_success") != snapshot["compile_success"]:
                changes.append(f"Compilation: {snapshot['compile_success']}")
            a, b = old.get("adoption"), snapshot["adoption"]
            if a is not None and b is not None and abs(a - b) + 1e-12 >= threshold:
                changes.append(f"Adoption: {a:.2%} → {b:.2%}")
        if changes and not baseline:
            event_meta = {"record_kind": record_kind, "algorithm_id": record["id"],
                          "stage": stage, "state": rs, "details": rd,
                          "adoption": snapshot["adoption"], "compile_success": snapshot["compile_success"],
                          "block_id": block["id"], "height": details["height"], "public": public}
            events.append(_event("submission", key, f"TIG {name}: {stage}", url,
                                 "; ".join(changes), event_meta))
    # Missing rows are not deletions: the live API may expose only a subset of
    # historical submissions. Keep successfully observed historical identities.
    all_records = copy.deepcopy(previous.get("records", {}))
    all_records.update(snapshots)
    state.update({"version": 1, "initialized": True,
                  "block": {"id": block["id"], "height": details["height"],
                            "round": details["round"], "timestamp": details.get("timestamp")},
                  "records": all_records})
    stats = {"baseline": baseline, "block_id": block["id"], "block_height": details["height"],
             "round": details["round"], "records": len(snapshots), "depth": depth,
             "coverage": "daily block snapshot; not an exhaustive block history",
             "source_checks": 0, "pending_sources": 0}
    if depth in {"light", "metadata"}:
        stats["source_coverage"] = "disabled at light/metadata depth"
        return {"state": state, "events": events, "stats": stats, "errors": errors}
    heads = _branches(client, repo, limits["max_branch_pages"])
    challenge_names = set(challenges.values())
    heads = {branch: sha for branch, sha in heads.items()
             if branch.count("/") == 1 and branch.split("/", 1)[0] in challenge_names}
    sources = copy.deepcopy(previous.get("sources", {}))
    baseline_heads = copy.deepcopy(previous.get("baseline_heads", {}))
    if baseline or "branch_heads" not in previous:
        # Enabling source monitoring later also establishes its own baseline.
        baseline_heads.update(heads)
    previous_heads = previous.get("branch_heads", {})
    for branch in sorted(set(previous_heads) - set(heads)):
        if not baseline:
            events.append(_event("branch_unavailable", branch,
                                 f"TIG public branch unavailable: {branch}",
                                 f"https://github.com/{repo}/branches", "Absent from a complete GitHub branch inventory; last known commit/source information retained.",
                                 {"branch": branch, "last_head": previous_heads[branch]}))
    def checked_head(branch):
        return sources.get(branch, {}).get("head", baseline_heads.get(branch))

    # The initial inventory is a baseline of branch commit SHAs, not a request
    # to download thousands of historical programs. Read the old commit lazily
    # only when that branch changes. Uninspected-but-unchanged is not an error.
    pending = [branch for branch, head in heads.items() if checked_head(branch) != head]
    attempts = copy.deepcopy(previous.get("source_attempts", {}))
    sequence = previous.get("source_scan_sequence", 0) + 1
    pending.sort(key=lambda branch: (attempts.get(branch, 0), branch))
    attempted = pending[:limits["max_source_checks"]]
    for branch in attempted:
        attempts[branch] = sequence
        old = sources.get(branch)
        try:
            if old is None and branch in baseline_heads:
                old, _, _, _ = _inspect_branch(client, repo, branch, baseline_heads[branch],
                                               None, limits, depth == "deep")
            inspected, changed, removed, detail = _inspect_branch(client, repo, branch, heads[branch], old, limits, depth == "deep")
            sources[branch] = inspected
            baseline_heads.pop(branch, None)
            if (changed or removed) and not baseline:
                events.append(_event("source", branch, f"TIG source updated: {branch}",
                                     f"https://github.com/{repo}/tree/{heads[branch]}/tig-algorithms",
                                     detail, {"branch": branch, "head": heads[branch],
                                              "previous_head": old.get("head") if old else previous_heads.get(branch),
                                              "changed_files": changed, "removed_files": removed,
                                              "source_fingerprint": _digest(inspected["files"])}))
        except Exception as exc:
            errors.append(f"TIG source {branch}: {type(exc).__name__}: {exc}")
    remaining = sorted(branch for branch, head in heads.items() if checked_head(branch) != head)
    state.update({"branch_heads": heads, "sources": sources, "baseline_heads": baseline_heads,
                  "source_attempts": attempts, "source_scan_sequence": sequence, "pending_sources": remaining})
    stats.update({"public_branches": len(heads), "source_checks": len(attempted),
                  "pending_sources": len(remaining), "source_errors": len(errors),
                  "baseline_content_uninspected": sum(branch in heads for branch in baseline_heads),
                  "source_coverage": "complete" if not remaining else "partial: pending branches are retained for later runs"})
    if remaining:
        errors.append(f"TIG source coverage incomplete: {len(remaining)} branches pending after {len(attempted)} checks (max_source_checks={limits['max_source_checks']}); no pending source snapshot was advanced")
    return {"state": state, "events": events, "stats": stats, "errors": errors}
