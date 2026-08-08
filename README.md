# Universal Web Monitoring Agent

This repository runs scheduled GitHub Actions jobs that monitor research sources and store results as Markdown reports:

- **LessWrong filter**: reads recent LessWrong posts (GraphQL API, with the RSS feed as a fallback) and filters for long ML/NLP posts with likely visualizations.
- **Page watchers**: scrape the Anthropic Interpretability team page and the Goodfire research page, detect new publication entries, and send an alert email on hits.
- **arXiv AI Top Papers** (weekly): ranks the last 7 days of AI-related arXiv submissions by reference count and by citation-weighted references.
- **Lab Publications filter** (temporarily disabled): matched arXiv papers against a list of lab names.

## Conventions

- **Fail loudly.** A monitor that cannot read its source must produce a red run, not an empty report. An empty report is indistinguishable from a quiet day and hides outages for as long as nobody looks; a visible gap in `reports/` is honest. `lw_filter.py` (`LW_STRICT`), `page_watch.py` (`WATCH_MIN_ENTRIES`) and `arxiv_top_papers.py` all follow this.
- **Log the evidence.** Every refused HTTP request logs its status, timing, the relevant response headers and a body snippet, so an incident is diagnosable from a single run's log.
- **Ask, don't evade.** These jobs identify themselves in the `User-Agent`, honour `robots.txt` (LessWrong asks for `Crawl-Delay: 3`) and keep to roughly one request a day. If a source deliberately blocks us, the response is to reduce load and contact the maintainers — never to rotate addresses, disguise the client or bypass a challenge.

## How it works

- GitHub Actions runs on a daily schedule or via `workflow_dispatch`.
- `lw_filter.py` requests recent LessWrong posts over HTTP instead of browser automation, using whichever transport answers (see below).
- The script filters posts by lookback window, minimum word count, visualization heuristics, and ML/NLP topic keywords.
- Matching posts are written to `reports/YYYY-MM-DD.md`.
- Processed post IDs are stored in `seen.json` so the workflow does not report the same post twice.

### Transports

`LW_TRANSPORT` selects how posts are fetched; the default `auto` tries them in order and reports the winner in the report header.

| Transport | Endpoint | Coverage | Notes |
| --- | --- | --- | --- |
| `graphql` | `POST /graphql` | Full window, paginated via `offset` | Preferred: one request returns `baseScore`, `wordCount`, `frontpageDate` and the full post HTML. Uses the `selector` argument; the older `input: { terms: … }` form is deprecated server-side. |
| `feed` | `GET /feed.xml?view=…` | 10 items per view, hard-capped server-side | Fallback. The feed's `<description>` is the same `contents.html` GraphQL returns and its `<guid>` is the bare post ID, so the filter heuristics and `seen.json` work unchanged. `baseScore` is unavailable and renders as `n/a`; a Frontpage post outside the 10 newest frontpage items is reported as `Personal/All (unconfirmed)`. Responses are CDN-cached, so this path can stay reachable when the uncacheable GraphQL POST is not — which is why **no `after` query parameter is sent**: it is part of the edge cache key, and a daily-changing value would force a MISS to the origin on every run. The lookback window is enforced client-side for both transports instead. |

### Failure behaviour

- A run that cannot fetch posts over any transport **writes no report, leaves `seen.json` untouched, exits non-zero** and sends the failure alert email. Set `LW_STRICT=0` to downgrade that to a green run with a degraded report.
- **The day's report is cumulative.** Reports are keyed by UTC date, and a second run of the same day only classifies posts absent from `seen.json` — so rewriting the file from that run's matches alone would silently delete the earlier ones, unrecoverably, since their IDs are already marked seen. Each day's matches are therefore also kept in `reports/.matches/YYYY-MM-DD.json`, and every run renders the union. A backlog catch-up run adds to the day's report instead of replacing it.
- If a report exists whose match index is missing (written before the index existed, or lost) and this run would write fewer matches than it lists, the write is **refused, the matched IDs are not recorded in `seen.json`**, and the run exits non-zero — so those posts stay eligible for the next run rather than being suppressed with nothing written anywhere.
- Total time spent waiting out retries is bounded by `LW_TIME_BUDGET_MINUTES`, deliberately below the workflow's `timeout-minutes`. GitHub enforces that timeout by *cancelling* the job, and cancellation would otherwise skip the alert step — so the script gives up first.
- Posts that arrive without body content are skipped **without** being marked seen, so a transport quirk cannot drop a post permanently.
- A refusal carrying no rate-limit budget headers, a non-JSON body and near-zero latency is treated as a standing edge block rather than a quota: the script stops retrying after `LW_BLOCK_STRIKES` attempts and moves to the next transport instead of burning its whole budget.
- The workflow's `Probe LessWrong reachability` step curls `/api/agent/ping` (LessWrong's [documented](https://www.lesswrong.com/api/SKILL.md) reachability probe), `/feed.xml` and `/graphql` on every run and never fails the job. If `/api/agent/ping` is refused as well, the whole host is unreachable from GitHub's runners and no transport change will help — the remedy is to contact the LessWrong developers (`POST /api/agent/feedback`, or an issue on [ForumMagnum](https://github.com/ForumMagnum/ForumMagnum)) and describe the job's traffic, not to work around the block.

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
- The unauthenticated pool is shared and often rate-limited (429) for minutes at a time; the job waits that out patiently (`S2_MAX_429_RETRIES`, up to ~30 min per request) instead of aborting. An overall time budget (`S2_TIME_BUDGET_MINUTES`, default 240) caps the Semantic Scholar phase: when exhausted, remaining per-paper reference fetches are skipped and the report is written with the data collected so far (affected papers keep their reference count but get no weighted score).
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
- `test_lw_filter.py`: offline unit tests for the LessWrong filter (transports, retry/blocking logic, report guards)
- `lab_pubs_filter.py`: arXiv lab-name filter (schedule temporarily disabled)
- `requirements.txt`: Python dependencies for the workflows and local runs
- `.github/workflows/`: scheduled workflows
- `reports/`: generated Markdown reports
- `seen.json`, `seen_labs.json`, `seen_anthropic_interpretability.json`, `seen_goodfire_research.json`: persisted state so nothing is reported twice

## Configuration (LessWrong filter)

Every value below can be set as a repository variable (Settings → Secrets and variables → Actions → Variables); `lesswrong-filter.yml` reads `vars.<NAME>` and falls back to the default. `LOOKBACK_DAYS`, `POST_LIMIT`, `LW_TRANSPORT` and `LW_STRICT` are also exposed as `workflow_dispatch` inputs, which take precedence — that is how you run a one-off backlog catch-up with a raised `POST_LIMIT`.

| Variable | Description | Default |
| --- | --- | --- |
| `MIN_WORDS` | Minimum word count for a matching post | `1800` |
| `LOOKBACK_DAYS` | How many days of posts to request | `14` |
| `POST_LIMIT` | Max posts fetched per run. Binds *before* `LOOKBACK_DAYS`: if more posts were published in the window than this, the window is truncated and the report says so. LessWrong publishes roughly 15–30 posts a day | `100` |
| `POST_SCOPE` | `all`, `frontpage`, or `personal` | `all` |
| `LW_TRANSPORT` | `auto`, `graphql`, or `feed` | `auto` |
| `LW_STRICT` | `1` fails the run when no transport works; `0` writes a degraded report instead | `1` |
| `LW_PAGE_SIZE` | Posts per GraphQL request when paginating | `50` |
| `LW_MAX_RETRIES` | Retry budget for network and 5xx errors | `5` |
| `LW_MAX_429_RETRIES` | Separate retry budget for rate limiting | `6` |
| `LW_MAX_BACKOFF_SECONDS` | Maximum computed wait between retries | `60` |
| `LW_RETRY_AFTER_CAP` | Hard ceiling on an honoured `Retry-After` header | `300` |
| `LW_BLOCK_STRIKES` | Consecutive budget-less refusals treated as a standing block | `2` |
| `LW_CRAWL_DELAY` | Minimum seconds between requests (`robots.txt` asks for 3) | `3` |
| `LW_TIME_BUDGET_MINUTES` | Total wall clock the run may spend waiting out retries, across all transports. Keep below the workflow's `timeout-minutes` | `10` |
| `LW_FEED_VIEWS` | Comma-separated RSS views for the feed transport | Derived from `POST_SCOPE` |
| `LESSWRONG_USER_AGENT` | Identifiable user agent for API requests | Repository URL based default |
| `LW_REQUEST_TIMEOUT` | Request timeout in seconds | `60` |

`LW_MAX_RETRIES`, `LW_MAX_BACKOFF_SECONDS` and `LW_REQUEST_TIMEOUT` also accept the older unprefixed names (`MAX_RETRIES`, `MAX_BACKOFF_SECONDS`, `REQUEST_TIMEOUT`); the prefixed ones win. The prefix exists because three scripts in this repository read those generic names with different intended defaults.

## Tests

`test_lw_filter.py` and `test_arxiv_top_papers.py` mock all HTTP, so they run offline and are executed by their workflows before the real job:

```bash
python -m unittest test_lw_filter.py test_arxiv_top_papers.py
```

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python lw_filter.py
```

## Notes

- LessWrong requests use an identifiable user agent and stay conservatively rate-limited (`LW_CRAWL_DELAY`, one run a day).
- The current implementation uses a heuristic topic and visualization filter so it can run without additional services.
- LessWrong has no API key or bot registration. GraphQL authentication is a `loginToken` harvested from a logged-in browser session, which would mean storing a live session credential in repository secrets — deliberately not done.
- LessWrong also publishes an agent-oriented Markdown API (`/api/SKILL.md`, `/api/latest`, `/api/post/[id]`). It is not used here because the filter's visualization heuristic counts HTML elements (`<figure>`, `<svg>`, `<canvas>`, `<iframe>`, `<table>`), which the Markdown rendering discards. `/api/agent/ping` and `/api/agent/feedback` from that same API are used for the reachability probe and as the escalation path.
