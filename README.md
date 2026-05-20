# Universal Web Monitoring Agent

This repository runs a scheduled GitHub Actions job that queries the LessWrong GraphQL API, filters for long ML/NLP posts with likely visualizations, and stores the daily results as Markdown reports.

## How it works

- GitHub Actions runs on a daily schedule or via `workflow_dispatch`.
- `lw_filter.py` requests recent LessWrong posts through GraphQL instead of browser automation.
- The script filters posts by lookback window, minimum word count, visualization heuristics, and ML/NLP topic keywords.
- Matching posts are written to `reports/YYYY-MM-DD.md`.
- Processed post IDs are stored in `seen.json` so the workflow does not report the same post twice.

## Repository files

- `lw_filter.py`: main LessWrong filtering script
- `requirements.txt`: Python dependencies for the workflow and local runs
- `.github/workflows/lesswrong-filter.yml`: scheduled workflow
- `reports/`: generated daily Markdown reports
- `seen.json`: persisted list of already processed LessWrong post IDs

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
