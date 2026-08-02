"""Weekly arXiv AI top-papers ranking.

Collects every paper submitted to the configured arXiv categories (default:
cs.AI, cs.LG, cs.CL, cs.NE) within the lookback window (default: 7 days) and
writes a Markdown report with two rankings:

1. Top N by number of references (first-order: how many works the paper cites).
2. Top N by citation-weighted references: every single reference is weighted
   by the current citation count of the cited work, i.e.
   score = sum(citation_count(ref) for ref in references).

Reference lists and citation counts come from the Semantic Scholar Graph API.
An API key (repository secret S2_API_KEY) is optional but raises the rate
limit; without one the script simply paces itself more conservatively.
"""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

OUT_DIR = Path("reports")

ARXIV_API = "https://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1"

CATEGORIES = [c.strip() for c in os.getenv("ARXIV_CATEGORIES", "cs.AI,cs.LG,cs.CL,cs.NE").split(",") if c.strip()]
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))
TOP_N = int(os.getenv("TOP_N", "15"))
MAX_PAPERS = int(os.getenv("MAX_PAPERS", "10000"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))

ARXIV_PAGE_SIZE = 1000
ARXIV_DELAY_SECONDS = float(os.getenv("ARXIV_DELAY_SECONDS", "3"))

S2_API_KEY = os.getenv("S2_API_KEY", "").strip()
S2_BATCH_SIZE = 500
S2_MIN_BATCH_SIZE = 25
# Unauthenticated requests share a global rate-limit pool, so pace harder.
S2_DELAY_SECONDS = float(os.getenv("S2_DELAY_SECONDS", "1.1" if S2_API_KEY else "3.0"))

USER_AGENT = "Universal-Web-Monitoring-Agent/1.0 (+https://github.com/erikiss/universal-web-monitoring-agent)"

ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

def bare_arxiv_id(entry_id):
    """'http://arxiv.org/abs/2507.12345v2' -> '2507.12345' (keeps old-style ids like 'cs/0112017')."""
    arxiv_id = re.sub(r"^https?://arxiv\.org/abs/", "", entry_id.strip())
    return re.sub(r"v\d+$", "", arxiv_id)


def arxiv_query(now):
    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    cat_clause = " OR ".join(f"cat:{c}" for c in CATEGORIES)
    date_clause = "submittedDate:[{} TO {}]".format(
        cutoff.strftime("%Y%m%d%H%M"), now.strftime("%Y%m%d%H%M")
    )
    return f"({cat_clause}) AND {date_clause}", cutoff


def fetch_arxiv_page(session, query, start, known_total=None):
    params = {
        "search_query": query,
        "start": start,
        "max_results": ARXIV_PAGE_SIZE,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    last_error = None
    for attempt in range(MAX_RETRIES):
        if attempt:
            time.sleep(min(2 ** attempt * 3, 60))
        try:
            resp = session.get(ARXIV_API, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            total, entries = parse_arxiv_feed(resp.content)
        except (requests.RequestException, ET.ParseError) as e:
            last_error = e
            log(f"arXiv page start={start} attempt {attempt + 1} failed: {e}")
            continue
        # The arXiv API sporadically returns an empty page — sometimes even
        # with totalResults=0 — although more results exist; treat both as
        # transient and retry before believing them.
        effective_total = max(total, known_total or 0)
        if not entries and (effective_total > start or (effective_total == 0 and attempt < 2)):
            last_error = RuntimeError(
                f"empty page at start={start} (totalResults={total}, known_total={known_total})")
            log(f"arXiv page start={start} attempt {attempt + 1}: transient empty page, retrying")
            continue
        return effective_total, entries
    raise RuntimeError(f"arXiv API failed for start={start}: {last_error}")


def parse_arxiv_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)
    total_el = root.find("opensearch:totalResults", ATOM_NS)
    total = int(total_el.text) if total_el is not None and total_el.text else 0

    papers = []
    for entry in root.findall("atom:entry", ATOM_NS):
        entry_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS).strip()
        if not entry_id:
            continue
        title = " ".join(entry.findtext("atom:title", default="", namespaces=ATOM_NS).split())
        published = entry.findtext("atom:published", default="", namespaces=ATOM_NS).strip()
        authors = [
            a.findtext("atom:name", default="", namespaces=ATOM_NS).strip()
            for a in entry.findall("atom:author", ATOM_NS)
        ]
        primary_el = entry.find("arxiv:primary_category", ATOM_NS)
        primary = primary_el.get("term") if primary_el is not None else ""
        categories = [c.get("term") for c in entry.findall("atom:category", ATOM_NS) if c.get("term")]
        papers.append({
            "arxiv_id": bare_arxiv_id(entry_id),
            "url": entry_id.replace("http://", "https://"),
            "title": title,
            "published": published,
            "authors": [a for a in authors if a],
            "primary_category": primary,
            "categories": categories,
        })
    return total, papers


def collect_papers(session, now):
    query, cutoff = arxiv_query(now)
    log(f"arXiv query: {query}")

    papers = {}
    start = 0
    total = None
    truncated = False
    while True:
        page_total, entries = fetch_arxiv_page(session, query, start, known_total=total)
        total = max(total or 0, page_total)
        if not entries:
            break
        for p in entries:
            try:
                published = datetime.fromisoformat(p["published"].replace("Z", "+00:00"))
            except ValueError:
                published = None
            # Belt and braces: the submittedDate clause already restricts the
            # window server-side, but drop anything outside it anyway.
            if published is not None and published < cutoff:
                continue
            papers.setdefault(p["arxiv_id"], p)
        start += len(entries)
        if start >= min(total, MAX_PAPERS) or len(papers) >= MAX_PAPERS:
            break
        time.sleep(ARXIV_DELAY_SECONDS)

    # Fail loudly instead of committing a silently short report if pagination
    # ended before reaching the announced total.
    if total and start < min(total, MAX_PAPERS) and len(papers) < MAX_PAPERS:
        raise RuntimeError(f"arXiv pagination ended early: fetched {start} of {total} results")

    if total and total > MAX_PAPERS:
        truncated = True
        log(f"WARNING: window contains {total} papers, only the newest {MAX_PAPERS} were fetched (MAX_PAPERS).")
    log(f"Collected {len(papers)} unique papers (arXiv totalResults={total}).")
    return list(papers.values()), total or 0, truncated


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------

class S2Client:
    def __init__(self, session):
        self.session = session
        self.last_request = 0.0
        self.headers = {"User-Agent": USER_AGENT}
        if S2_API_KEY:
            self.headers["x-api-key"] = S2_API_KEY

    def _throttle(self):
        wait = self.last_request + S2_DELAY_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()

    def request(self, method, url, *, params=None, json_body=None):
        """Returns the parsed JSON. Retries 429/5xx/network errors; raises
        requests.HTTPError for persistent 4xx so callers can degrade."""
        last_error = None
        for attempt in range(MAX_RETRIES):
            self._throttle()
            try:
                resp = self.session.request(
                    method, url, params=params, json=json_body,
                    headers=self.headers, timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as e:
                last_error = e
                log(f"S2 {method} {url} attempt {attempt + 1} failed: {e}")
                time.sleep(min(2 ** attempt * 2, 60))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 120)
                except (TypeError, ValueError):
                    delay = min(2 ** attempt * 5, 120)
                last_error = requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                log(f"S2 {method} {url}: HTTP {resp.status_code}, backing off {delay:.0f}s")
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"S2 API failed after {MAX_RETRIES} attempts: {last_error}")


def s2_batch(client, arxiv_ids, fields):
    """POST /paper/batch for one chunk. Returns a list aligned with arxiv_ids
    (None for papers unknown to Semantic Scholar). Raises on hard failure."""
    result = client.request(
        "POST",
        f"{S2_API}/paper/batch",
        params={"fields": fields},
        json_body={"ids": [f"arXiv:{a}" for a in arxiv_ids]},
    )
    if not isinstance(result, list) or len(result) != len(arxiv_ids):
        raise RuntimeError(f"unexpected batch response shape for {len(arxiv_ids)} ids")
    return result


NESTED_FIELDS = "paperId,title,externalIds,referenceCount,citationCount,references.citationCount"
SCALAR_FIELDS = "paperId,title,externalIds,referenceCount,citationCount"


def fetch_s2_records(client, arxiv_ids):
    """Fetch S2 metadata for all papers.

    Returns dict: arxiv_id -> {"found": bool, "reference_count": int|None,
    "citation_count": int|None, "ref_citations": list[int]|None}.
    ref_citations is None when the reference list could not be fetched.
    """
    records = {}
    chunks = [arxiv_ids[i:i + S2_BATCH_SIZE] for i in range(0, len(arxiv_ids), S2_BATCH_SIZE)]
    state = {"nested_ok": True}

    def handle_chunk(chunk):
        fields = NESTED_FIELDS if state["nested_ok"] else SCALAR_FIELDS
        try:
            return s2_batch(client, chunk, fields)
        except requests.HTTPError as e:
            # Splitting helps when the response was too large; if even a
            # minimum-size chunk fails with nested fields, the fields
            # themselves are the problem — drop them for the rest of the run
            # (the per-paper fallback below recovers the reference lists).
            if len(chunk) > S2_MIN_BATCH_SIZE:
                log(f"S2 batch of {len(chunk)} failed ({e}); splitting")
                mid = len(chunk) // 2
                return handle_chunk(chunk[:mid]) + handle_chunk(chunk[mid:])
            if state["nested_ok"]:
                log(f"S2 batch of {len(chunk)} failed with nested fields ({e}); disabling nested reference fetch")
                state["nested_ok"] = False
                return handle_chunk(chunk)
            raise

    for i, chunk in enumerate(chunks):
        log(f"S2 batch chunk {i + 1}/{len(chunks)} ({len(chunk)} papers)")
        for arxiv_id, item in zip(chunk, handle_chunk(chunk)):
            if item is None:
                records[arxiv_id] = {"found": False, "reference_count": None,
                                     "citation_count": None, "ref_citations": None}
                continue
            ref_count = item.get("referenceCount")
            refs = item.get("references")
            ref_citations = None
            if isinstance(refs, list):
                ref_citations = [r.get("citationCount") or 0 for r in refs if isinstance(r, dict)]
                # Nested reference lists can be truncated; fall back to the
                # paginated endpoint when fewer entries came back than exist.
                if ref_count is not None and len(ref_citations) < ref_count:
                    ref_citations = None
            records[arxiv_id] = {
                "found": True,
                "reference_count": ref_count,
                "citation_count": item.get("citationCount"),
                "ref_citations": ref_citations,
            }

    # Per-paper fallback for missing/truncated reference lists.
    pending = [a for a in arxiv_ids
               if records[a]["found"] and records[a]["ref_citations"] is None
               and (records[a]["reference_count"] or 0) > 0]
    consecutive_failures = 0
    for i, arxiv_id in enumerate(pending):
        log(f"S2 reference fallback {i + 1}/{len(pending)}: {arxiv_id}")
        try:
            records[arxiv_id]["ref_citations"] = fetch_references(client, arxiv_id)
            consecutive_failures = 0
        except (requests.HTTPError, RuntimeError) as e:
            log(f"WARNING: could not fetch references for {arxiv_id}: {e}")
            consecutive_failures += 1
            if consecutive_failures >= 10:
                log("WARNING: 10 consecutive reference fetches failed; skipping the remaining fallback queue.")
                break
    return records


def fetch_references(client, arxiv_id):
    """GET /paper/arXiv:{id}/references with offset pagination."""
    citations = []
    offset = 0
    while True:
        result = client.request(
            "GET",
            f"{S2_API}/paper/arXiv:{arxiv_id}/references",
            params={"fields": "citationCount", "limit": 1000, "offset": offset},
        )
        for item in result.get("data", []):
            cited = item.get("citedPaper") or {}
            citations.append(cited.get("citationCount") or 0)
        next_offset = result.get("next")
        if next_offset is None or next_offset <= offset:
            return citations
        offset = next_offset


# ---------------------------------------------------------------------------
# Ranking and report
# ---------------------------------------------------------------------------

def build_rankings(papers, records):
    """Attach metrics to papers and return (ranked_by_refs, ranked_by_weight)."""
    measurable = []
    for p in papers:
        rec = records.get(p["arxiv_id"])
        if not rec or not rec["found"]:
            continue
        ref_citations = rec["ref_citations"]
        if ref_citations is not None:
            ref_count = len(ref_citations)
            weighted = sum(ref_citations)
        elif rec["reference_count"] is not None:
            ref_count = rec["reference_count"]
            weighted = 0 if ref_count == 0 else None
        else:
            continue
        entry = dict(p)
        entry["ref_count"] = ref_count
        entry["weighted_score"] = weighted
        entry["citation_count"] = rec["citation_count"]
        measurable.append(entry)

    by_refs = sorted(
        measurable,
        key=lambda e: (-e["ref_count"], -(e["weighted_score"] or 0), e["title"]),
    )
    weighable = [e for e in measurable if e["weighted_score"] is not None]
    by_weight = sorted(
        weighable,
        key=lambda e: (-e["weighted_score"], -e["ref_count"], e["title"]),
    )
    return measurable, by_refs[:TOP_N], by_weight[:TOP_N]


def md_escape(text):
    # Backslash, brackets and pipe would otherwise break the link syntax or
    # the table cell for titles like "Results on [MASK] | a study".
    return re.sub(r"([\\\[\]|])", r"\\\1", text)


def format_row(rank, entry):
    authors = entry["authors"]
    author_str = md_escape(authors[0]) + (" et al." if len(authors) > 1 else "") if authors else "—"
    published = entry["published"][:10] if entry["published"] else "—"
    weighted = f"{entry['weighted_score']:,}" if entry["weighted_score"] is not None else "n/a"
    return (
        f"| {rank} | [{md_escape(entry['title'])}]({entry['url']})<br>{author_str} "
        f"| {entry['primary_category'] or '—'} | {published} "
        f"| {entry['ref_count']} | {weighted} |"
    )


TABLE_HEADER = [
    "| # | Paper | Primary cat. | Published | References | Weighted score |",
    "| --- | --- | --- | --- | --- | --- |",
]


def build_report(now, papers, records, measurable, by_refs, by_weight, arxiv_total, truncated):
    today = now.strftime("%Y-%m-%d")
    window_start = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    found = sum(1 for r in records.values() if r["found"])
    with_refs = sum(1 for r in records.values() if r["ref_citations"] is not None)

    lines = [f"# Weekly arXiv AI Top Papers — {today}", ""]
    lines.append(f"- **Window**: {window_start} → {today} ({LOOKBACK_DAYS} days, submission date)")
    lines.append(f"- **Categories**: {', '.join(CATEGORIES)}")
    lines.append(f"- **Papers in window**: {len(papers)}" + (f" (of {arxiv_total}; truncated by MAX_PAPERS)" if truncated else ""))
    lines.append(f"- **Found on Semantic Scholar**: {found}")
    lines.append(f"- **With full reference data**: {with_refs}")
    lines.append("")

    lines.append(f"## Top {TOP_N} by number of references")
    lines.append("")
    lines.append("Ranked by how many works each paper cites (first-order reference count).")
    lines.append("")
    if by_refs:
        lines.extend(TABLE_HEADER)
        lines.extend(format_row(i + 1, e) for i, e in enumerate(by_refs))
    else:
        lines.append("_No papers with reference data in this window._")
    lines.append("")

    lines.append(f"## Top {TOP_N} by citation-weighted references")
    lines.append("")
    lines.append(
        "Every reference is weighted by the current citation count of the cited work: "
        "**weighted score = Σ citation_count(reference)** over all references of the paper."
    )
    lines.append("")
    if by_weight:
        lines.extend(TABLE_HEADER)
        lines.extend(format_row(i + 1, e) for i, e in enumerate(by_weight))
    else:
        lines.append("_No papers with reference data in this window._")
    lines.append("")

    lines.append("## Method & caveats")
    lines.append("")
    lines.append("- Paper list from the arXiv API (`submittedDate` window over the categories above, cross-listings deduplicated).")
    lines.append("- Reference lists and citation counts from the Semantic Scholar Graph API at report time.")
    lines.append("- Papers submitted in the last few days may not be indexed by Semantic Scholar yet, or their bibliographies may not be parsed yet; such papers cannot be ranked and are only reflected in the coverage numbers above.")
    lines.append("- References that Semantic Scholar cannot match to an indexed work count as 0 in the weighted score.")
    lines.append(f"- Rankings cover the {len(measurable)} papers with Semantic Scholar data.")
    return "\n".join(lines).rstrip() + "\n"


def main():
    OUT_DIR.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    papers, arxiv_total, truncated = collect_papers(session, now)
    if not papers:
        log("No papers found in window; writing empty report.")

    client = S2Client(session)
    records = fetch_s2_records(client, [p["arxiv_id"] for p in papers]) if papers else {}

    measurable, by_refs, by_weight = build_rankings(papers, records)
    report = build_report(now, papers, records, measurable, by_refs, by_weight, arxiv_total, truncated)

    report_path = OUT_DIR / f"arxiv_top{TOP_N}_{now.strftime('%Y-%m-%d')}.md"
    report_path.write_text(report, encoding="utf-8")
    log(f"Wrote {report_path} ({len(measurable)} ranked papers, top lists of {len(by_refs)}/{len(by_weight)}).")


if __name__ == "__main__":
    main()
