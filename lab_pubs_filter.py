import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import json
import re

OUT_DIR = Path("reports")
SEEN_FILE = Path("seen_labs.json")
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "3"))

# The user explicitly listed 51 items:
LAB_SEARCH_TERMS = {
    "Ai2 – Allen Institute for AI": [r"\bai2\b", r"allen institute for (?:artificial intelligence|ai)\b"],
    "Apollo Research": [r"apollo research"],
    "Arc Institute": [r"arc institute"],
    "Anthropic Interpretability / Anthropic Safety Research": [r"anthropic interpretability", r"anthropic safety"],
    "Alignment Research Center / ARC": [r"alignment research center", r"\barc\b.*alignment", r"alignment.*\barc\b"],
    "AISI – UK AI Security Institute": [r"uk ai security institute", r"uk ai safety institute", r"\baisi\b"],
    "Berkeley BAIR – Berkeley Artificial Intelligence Research": [r"berkeley artificial intelligence research", r"\bbair\b"],
    "Berkeley CHAI – Center for Human-Compatible AI": [r"center for human-compatible ai", r"\bchai\b"],
    "CMU AI Ecosystem": [r"carnegie mellon university", r"\bcmu\b"],
    "CMU Machine Learning Department": [r"cmu machine learning", r"machine learning department.*cmu", r"machine learning department.*carnegie mellon"],
    "CMU Robotics Institute": [r"cmu robotics", r"robotics institute.*cmu", r"robotics institute.*carnegie mellon"],
    "CMU Language Technologies Institute": [r"language technologies institute"],
    "Cyber Valley": [r"cyber valley"],
    "DFKI – Deutsches Forschungszentrum für Künstliche Intelligenz": [r"deutsches forschungszentrum f[uü]r k[uü]nstliche intelligenz", r"\bdfki\b"],
    "ELLIS Institute Tübingen": [r"ellis institute t[uü]bingen"],
    "ELLIS Network": [r"ellis network", r"\bellis\b"],
    "FAR.AI": [r"far\.ai", r"foundation for alignment research"],
    "FutureHouse": [r"futurehouse", r"future house"],
    "Goodfire": [r"goodfire"],
    "Google DeepMind Science": [r"google deepmind science"],
    "Harvard Kempner Institute": [r"kempner institute"],
    "Institute for Protein Design, University of Washington": [r"institute for protein design"],
    "Isomorphic Labs": [r"isomorphic labs"],
    "KE:SAI – Center for Knowledge-Driven AI Systems": [r"center for knowledge-driven ai systems", r"ke:sai"],
    "Kyutai": [r"\bkyutai\b"],
    "Latent Labs": [r"latent labs"],
    "Liquid AI": [r"liquid ai"],
    "Meta FAIR – Fundamental AI Research": [r"fundamental ai research", r"\bfair\b.*meta", r"meta.*\bfair\b"],
    "Meta AI Research": [r"meta ai research", r"meta ai\b"],
    "METR – Model Evaluation & Threat Research": [r"\bmetr\b", r"model evaluation [&and] threat research"],
    "Mila – Quebec AI Institute": [r"\bmila\b", r"quebec ai institute", r"institut qu[eé]b[eé]cois d'intelligence artificielle"],
    "MIT CSAIL": [r"\bcsail\b", r"computer science and artificial intelligence laboratory"],
    "MPI-IS – Max Planck Institute for Intelligent Systems": [r"mpi-is", r"max planck institute for intelligent systems"],
    "Microsoft Research AI Frontiers": [r"microsoft research ai frontiers"],
    "OpenAI Research": [r"\bopenai\b"],
    "Periodic Labs": [r"periodic labs"],
    "Physical Intelligence": [r"physical intelligence"],
    "Redwood Research": [r"redwood research"],
    "RIKEN AIP – Center for Advanced Intelligence Project": [r"riken aip", r"center for advanced intelligence project"],
    "Sakana AI": [r"sakana ai"],
    "Santa Fe Institute": [r"santa fe institute"],
    "Stanford AI Lab / Stanford AI ecosystem": [r"stanford ai lab", r"\bsail\b.*stanford", r"stanford artificial intelligence laboratory"],
    "Stanford CRFM – Center for Research on Foundation Models": [r"stanford crfm", r"center for research on foundation models"],
    "Stanford HAI – Institute for Human-Centered AI": [r"stanford hai", r"institute for human-centered ai", r"institute for human-centered artificial intelligence"],
    "Toyota Research Institute Robotics": [r"toyota research institute"],
    "Tübingen AI Center": [r"t[uü]bingen ai center"],
    "Vector Institute": [r"vector institute"],
    "xAI": [r"\bxai\b"],
    "Google DeepMind": [r"deepmind"],
    "Anthropic": [r"\banthropic\b"],
    "Meta Superintelligence Labs": [r"meta superintelligence"]
}

# Compile regexes
COMPILED_LABS = {k: [re.compile(pattern, re.IGNORECASE) for pattern in v] for k, v in LAB_SEARCH_TERMS.items()}

def load_seen():
    if not SEEN_FILE.exists():
        return set()
    return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))

def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(list(seen)), indent=2), encoding="utf-8")

def fetch_arxiv_papers(start=0, max_results=1000):
    url = f"http://export.arxiv.org/api/query?search_query=cat:cs.*+OR+cat:stat.ML&sortBy=submittedDate&sortOrder=desc&start={start}&max_results={max_results}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Universal-Web-Monitoring-Agent/1.0'})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()
        except Exception as e:
            if attempt == 4:
                print(f"Failed to fetch arxiv: {e}")
                return b""
            time.sleep(5)
    return b""

def parse_papers(xml_data):
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return []
    
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    papers = []
    
    for entry in root.findall('atom:entry', ns):
        paper_id = entry.find('atom:id', ns).text
        title = entry.find('atom:title', ns).text.replace('\n', ' ').strip()
        summary = entry.find('atom:summary', ns).text.replace('\n', ' ').strip()
        published = entry.find('atom:published', ns).text
        
        authors = []
        for author in entry.findall('atom:author', ns):
            name = author.find('atom:name', ns).text
            authors.append(name)
            
        papers.append({
            'id': paper_id,
            'title': title,
            'summary': summary,
            'published': published,
            'authors': authors
        })
    return papers

def get_matching_labs(paper):
    text_to_search = f"{paper['title']} {paper['summary']} {' '.join(paper['authors'])}"
    matched_labs = set()
    for lab, regexes in COMPILED_LABS.items():
        for r in regexes:
            if r.search(text_to_search):
                matched_labs.add(lab)
                break
    return matched_labs

def main():
    OUT_DIR.mkdir(exist_ok=True)
    seen = load_seen()
    
    # We will fetch up to 2000 recent papers
    papers = []
    for start in [0, 1000]:
        xml_data = fetch_arxiv_papers(start=start, max_results=1000)
        if xml_data:
            papers.extend(parse_papers(xml_data))
        time.sleep(3) # Be nice to arxiv api
    
    # Filter by date
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    recent_papers = []
    for p in papers:
        try:
            pub_date = datetime.fromisoformat(p['published'].replace('Z', '+00:00'))
            if pub_date >= cutoff:
                recent_papers.append(p)
        except ValueError:
            pass

    # Match labs
    # Structure: dict[lab] = [list of papers]
    lab_matches = {lab: [] for lab in LAB_SEARCH_TERMS.keys()}
    new_matches = 0
    
    for paper in recent_papers:
        if paper['id'] in seen:
            continue
            
        matches = get_matching_labs(paper)
        if matches:
            for lab in matches:
                lab_matches[lab].append(paper)
            seen.add(paper['id'])
            new_matches += 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = OUT_DIR / f"labs_{today}.md"
    
    lines = [f"# Daily Lab Publications Report — {today}", ""]
    lines.append(f"Lookback: {LOOKBACK_DAYS} days")
    lines.append(f"New matched papers: {new_matches}")
    lines.append("")
    
    # Write groups
    for lab in sorted(lab_matches.keys()):
        pubs = lab_matches[lab]
        if not pubs:
            continue
        lines.append(f"## {lab}")
        for pub in pubs:
            author_str = ", ".join(pub['authors'][:5]) + (" et al." if len(pub['authors']) > 5 else "")
            lines.append(f"### [{pub['title']}]({pub['id']})")
            lines.append(f"- **Authors**: {author_str}")
            lines.append(f"- **Published**: {pub['published']}")
            lines.append(f"- **Abstract**: {pub['summary'][:300]}...")
            lines.append("")
            
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    save_seen(seen)
    print(f"Wrote {report_path} with {new_matches} new papers.")

if __name__ == "__main__":
    main()
