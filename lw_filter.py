from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

ENDPOINT = "https://www.lesswrong.com/graphql"
OUT_DIR = Path("reports")
SEEN_FILE = Path("seen.json")
MIN_WORDS = int(os.getenv("MIN_WORDS", "1800"))
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "14"))
POST_SCOPE = os.getenv("POST_SCOPE", "all").strip().lower()
USER_AGENT = os.getenv(
    "LESSWRONG_USER_AGENT",
    "Universal-Web-Monitoring-Agent/1.0 (+https://github.com/Erikiss/Universal-Web-Monitoring-Agent)",
)
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

TOPIC_RE = re.compile(
    r"\b("
    r"machine learning|deep learning|neural network|neural networks|"
    r"large language model|language model|llm|llms|gpt|claude|"
    r"nlp|natural language|transformer|embedding|embeddings|"
    r"reinforcement learning|rl|mechanistic interpretability|"
    r"sparse autoencoder|sae|ai alignment|alignment|"
    r"classifier|gradient|loss function|scaling law"
    r")\b",
    re.IGNORECASE,
)
VIS_RE = re.compile(
    r"\b("
    r"diagram|chart|plot|graph|matrix|correlation|heatmap|"
    r"figure|visualization|visualisation|scatter|histogram|"
    r"table|axis|axes|simulation"
    r")\b",
    re.IGNORECASE,
)
QUERY = """
query RecentPosts($limit: Int!, $after: String) {
  posts(input: { terms: { view: "new", limit: $limit, after: $after } }) {
    results {
      _id
      title
      slug
      postedAt
      frontpageDate
      baseScore
      wordCount
      contents {
        html
      }
      user {
        displayName
        username
      }
    }
  }
}
"""


def load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()

    raw = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{SEEN_FILE} must contain a JSON array of post IDs")
    return {str(post_id) for post_id in raw}



def save_seen(seen: set[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2) + "\n", encoding="utf-8")



def graphql(query: str, variables: dict[str, Any], max_retries: int = 5) -> dict[str, Any]:
    for attempt in range(max_retries):
        response = requests.post(
            ENDPOINT,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            json={"query": query, "variables": variables},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                wait_time = int(retry_after) if retry_after is not None else (2**attempt)
            except ValueError:
                wait_time = 2**attempt
            time.sleep(wait_time if wait_time > 0 else (2**attempt))
            continue
        response.raise_for_status()
        data = response.json()
        if "errors" in data:
            raise RuntimeError(data["errors"])
        return data["data"]
    raise RuntimeError("Exceeded maximum retries due to rate limiting.")



def html_text(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)



def estimate_words(post: dict[str, Any]) -> int:
    if post.get("wordCount"):
        return int(post["wordCount"])
    return len(re.findall(r"\b\w+\b", html_text((post.get("contents") or {}).get("html", ""))))



def visualization_score(html: str) -> tuple[int, list[str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    score = 0
    evidence: list[str] = []

    for tag in soup.find_all(["img", "figure", "svg", "canvas", "iframe", "table"]):
        previous_text = " ".join(
            sibling.get_text(" ", strip=True)
            for sibling in tag.find_previous_siblings(limit=2)
            if hasattr(sibling, "get_text")
        )
        next_text = " ".join(
            sibling.get_text(" ", strip=True)
            for sibling in tag.find_next_siblings(limit=2)
            if hasattr(sibling, "get_text")
        )
        text = " ".join(
            [
                tag.name,
                tag.get("alt", ""),
                tag.get("title", ""),
                tag.get("aria-label", ""),
                tag.get("src", ""),
                tag.get_text(" ", strip=True),
                previous_text,
                next_text,
            ]
        )

        score += 2 if tag.name in {"svg", "canvas", "table"} else 1
        if VIS_RE.search(text):
            score += 2
        evidence.append(text[:180])

    return score, evidence[:5]



def topic_score(title: str, text: str) -> int:
    haystack = f"{title}\n{text[:8000]}"
    return len(TOPIC_RE.findall(haystack))



def post_url(post: dict[str, Any]) -> str:
    slug = post.get("slug", "")
    return f"https://www.lesswrong.com/posts/{post['_id']}/{slug}".rstrip("/")



def include_post(post: dict[str, Any]) -> bool:
    if POST_SCOPE == "all":
        return True
    is_frontpage = bool(post.get("frontpageDate"))
    if POST_SCOPE == "frontpage":
        return is_frontpage
    if POST_SCOPE == "personal":
        return not is_frontpage
    raise ValueError("POST_SCOPE must be one of: all, frontpage, personal")



def classify(post: dict[str, Any]) -> dict[str, Any] | None:
    if not include_post(post):
        return None

    html = ((post.get("contents") or {}).get("html") or "").strip()
    text = html_text(html)
    words = estimate_words(post)
    if words < MIN_WORDS:
        return None

    vis_score, vis_evidence = visualization_score(html)
    if vis_score < 2:
        return None

    t_score = topic_score(post.get("title", ""), text)
    if t_score < 1:
        return None

    user = post.get("user") or {}
    author = user.get("displayName") or user.get("username") or "unknown"
    return {
        "id": str(post["_id"]),
        "title": post.get("title", "Untitled"),
        "url": post_url(post),
        "postedAt": post.get("postedAt"),
        "author": author,
        "section": "Frontpage" if post.get("frontpageDate") else "Personal/All",
        "wordCount": words,
        "baseScore": post.get("baseScore"),
        "visualizationScore": vis_score,
        "topicScore": t_score,
        "visualEvidence": vis_evidence,
    }



def render_report(matches: list[dict[str, Any]], today: str) -> str:
    lines = [f"# LessWrong ML/NLP visual long-post filter — {today}", ""]
    lines.append(f"Lookback: {LOOKBACK_DAYS} days")
    lines.append(f"Minimum words: {MIN_WORDS}")
    lines.append(f"Post scope: {POST_SCOPE}")
    lines.append("")
    lines.append(f"Matches: {len(matches)}")
    lines.append("")

    for match in sorted(
        matches,
        key=lambda item: (item["topicScore"], item["visualizationScore"], item["wordCount"]),
        reverse=True,
    ):
        lines.append(f"## [{match['title']}]({match['url']})")
        lines.append(f"- Author: {match['author']}")
        lines.append(f"- Section: {match['section']}")
        lines.append(f"- Posted: {match['postedAt']}")
        lines.append(f"- Words: {match['wordCount']}")
        lines.append(f"- Karma/baseScore: {match['baseScore']}")
        lines.append(f"- Topic score: {match['topicScore']}")
        lines.append(f"- Visualization score: {match['visualizationScore']}")
        if match["visualEvidence"]:
            lines.append("- Visualization evidence:")
            for snippet in match["visualEvidence"]:
                lines.append(f"  - {snippet}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"



def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    after = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    posts = graphql(QUERY, {"limit": 100, "after": after})["posts"]["results"]
    seen = load_seen()
    matches: list[dict[str, Any]] = []

    for post in posts:
        post_id = str(post["_id"])
        if post_id in seen:
            continue
        result = classify(post)
        if result:
            matches.append(result)
        seen.add(post_id)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = OUT_DIR / f"{today}.md"
    report_path.write_text(render_report(matches, today), encoding="utf-8")
    save_seen(seen)
    print(f"Wrote {report_path} with {len(matches)} matches.")


if __name__ == "__main__":
    main()
