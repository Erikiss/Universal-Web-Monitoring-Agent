# Universal Web Monitoring Agent

This repository runs scheduled GitHub Actions jobs that monitor research sources and store results as Markdown reports:

- **LessWrong filter**: queries the LessWrong GraphQL API and filters for long ML/NLP posts with likely visualizations.
- **Page watchers**: scrape the Anthropic Interpretability team page and the Goodfire research page, detect new publication entries, and send an alert email on hits.
- **arXiv AI Top Papers** (weekly): ranks the last 7 days of AI-related arXiv submissions by reference count and by citation-weighted references.
- **Lab Publications filter** (temporarily disabled): matched arXiv papers against a list of lab names.

## How it works

- GitHub Actions runs on a daily schedule or via `workflow_dispatch`.
- `lw_filter.py` requests recent LessWrong posts through GraphQL instead of browser automation.
- The script filters posts by lookback window, minimum word count, visualization heuristics, and ML/NLP topic keywords.
- Matching posts are written to `reports/YYYY-MM-DD.md`.
- Processed post IDs are stored in `seen.json` so the workflow does not report the same post twice.

## Page watchers (Anthropic Interpretability & Goodfire research)

Two separate workflows watch one concrete URL each instead of matching names on arXiv:

- `anthropic-interpretability-watch.yml` → https://www.anthropic.com/research/team/interpretability (3×/day at 06:00, 14:00, 22:00 UTC = 00/08/16h CEST)
- `goodfire-research-watch.yml` → https://www.goodfire.ai/research (3×/day at 06:15, 14:15, 22:15 UTC, staggered to avoid push races)

Both run `page_watch.py`, which extracts publication links from the page (rendered anchors plus URLs embedded in script/JSON payloads), compares them against a persisted baseline (`seen_anthropic_interpretability.json` / `seen_goodfire_research.json`), and writes a report to `reports/` only when something changed. Design decisions:

- **Fail loudly**: if fewer entries than a per-site minimum can be extracted (page redesign, bot block, client-only rendering), the run fails instead of silently treating a degraded page as "no news". Override with the `WATCH_MIN_ENTRIES` environment variable.
- **Baseline on first run**: the first run records all current entries without alerting; only entries appearing later trigger the alert email.
- Reports are only written on baseline initialization or new hits, so the `reports/` directory is not flooded with empty daily files.

### Alert email (repository secrets)

On a hit, `send_alert.py` sends a plain-text email via SMTP. All personal data lives exclusively in GitHub Actions repository secrets (Settings → Secrets and variables → Actions), so nothing private appears in this public repository or its logs:

| Secret | Required | Description |
| --- | --- | --- |
| `SMTP_HOST` | yes | SMTP server of the sending account |
| `SMTP_PORT` | no | `587` (STARTTLS, default) or `465` (implicit TLS) |
| `SMTP_USERNAME` | yes | Login of the sending account |
| `SMTP_PASSWORD` | yes | Password / app password of the sending account |
| `ALERT_EMAIL_TO` | yes | Recipient address(es), comma-separated — kept secret on purpose |
| `ALERT_EMAIL_FROM` | no | From address, defaults to `SMTP_USERNAME` |

If a hit occurs while these secrets are missing, the email step fails visibly (listing only the missing secret *names*) so a hit can never be dropped silently. Both workflows accept two *Run workflow* inputs: `dry_run` tests scraping without committing or emailing, and `test_email` sends a test alert immediately so the SMTP secrets can be verified without waiting for a real hit.

## arXiv AI Top Papers (weekly)

`arxiv_top_papers.py` runs once a week (`arxiv-top-papers.yml`, Sundays 22:30 UTC) and covers everything submitted to the AI/computing categories `cs.AI`, `cs.LG`, `cs.CL`, `cs.NE` (override with `ARXIV_CATEGORIES`) during the last 7 days (`LOOKBACK_DAYS`). It writes `reports/arxiv_top15_YYYY-MM-DD.md` with two rankings:

1. **Top 15 by number of references** — first-order: how many works each paper cites.
2. **Top 15 by citation-weighted references** — every single reference is weighted by the current citation count of the cited work: `weighted score = Σ citation_count(reference)`.

Reference lists and citation counts come from the [Semantic Scholar Graph API](https://api.semanticscholar.org/api-docs/graph). Notes:

- The optional repository secret `S2_API_KEY` (a free Semantic Scholar API key) raises the rate limit considerably; without it the script paces itself more conservatively and the run takes longer. An invalid or not-yet-activated key (Semantic Scholar answers 403) is detected at runtime: the job logs a warning and falls back to unauthenticated requests instead of failing.
- Very fresh papers may not be indexed by Semantic Scholar yet (or their bibliographies may not be parsed yet); the report header shows exactly how many papers could be ranked.
- The job is stateless: each run is a self-contained weekly snapshot, so there is no `seen_*.json` file.
- Offline unit tests (`test_arxiv_top_papers.py`, mocked APIs) run in the workflow before the ranking step.

## Lab Publications filter (temporarily disabled)

`lab_pubs_filter.py` matched recent arXiv papers against a large list of lab *names* (no URLs) and produced only empty reports for weeks. Its schedule is therefore commented out in `.github/workflows/lab-pubs-filter.yml`; it can still be started manually via `workflow_dispatch` and re-enabled by uncommenting the `schedule` block.

## Repository files

- `lw_filter.py`: main LessWrong filtering script
- `page_watch.py`: generic page watcher used by the Anthropic/Goodfire workflows
- `send_alert.py`: SMTP alert mailer (configured via repository secrets)
- `arxiv_top_papers.py`: weekly arXiv AI top-papers ranking (references & citation-weighted references)
- `test_arxiv_top_papers.py`: offline unit tests for the ranking script
- `lab_pubs_filter.py`: arXiv lab-name filter (schedule temporarily disabled)
- `requirements.txt`: Python dependencies for the workflows and local runs
- `.github/workflows/`: scheduled workflows
- `reports/`: generated Markdown reports
- `seen.json`, `seen_labs.json`, `seen_anthropic_interpretability.json`, `seen_goodfire_research.json`: persisted state so nothing is reported twice

## Configuration

The workflow can be configured with repository variables or step-level environment values:

| Variable | Description | Default |
| --- | --- | --- |
| `MIN_WORDS` | Minimum word count for a matching post | `1800` |
| `LOOKBACK_DAYS` | How many days of posts to request | `14` |
| `POST_LIMIT` | Max number of recent posts requested per run | `30` |
| `POST_SCOPE` | `all`, `frontpage`, or `personal` | `all` |
| `MAX_RETRIES` | Number of retry attempts for transient API errors/rate limits | `5` |
| `MAX_BACKOFF_SECONDS` | Maximum wait between retries in seconds | `60` |
| `LESSWRONG_USER_AGENT` | Identifiable user agent for API requests | Repository URL based default |
| `REQUEST_TIMEOUT` | Request timeout in seconds | `30` |

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python lw_filter.py
```

## Notes

- LessWrong requests should use an identifiable user agent and stay conservatively rate-limited.
- The current implementation uses a heuristic topic and visualization filter so it can run without additional services.
