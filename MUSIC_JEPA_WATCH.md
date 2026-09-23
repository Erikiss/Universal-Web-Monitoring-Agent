# Music-JEPA release watch

Daily monitoring for **Music-JEPA: Learning a World Model of Sound from Action**
(arXiv **2607.22000**). This is an independent watcher; the existing Goodfire and
Anthropic workflows, their state, schedules, and `send_alert.py` are unchanged.

## Schedule and notifications

Workflow: `.github/workflows/music-jepa-release-watch.yml`.
Runs daily at **07:37 UTC** (09:37 CEST / 08:37 CET); GitHub Actions may start late.
It also runs after changes to its implementation/workflow on `main`, and supports
manual **Run workflow** with `dry_run` and `test_email` switches.

Alerts reuse `send_alert.py` and the existing repository secrets:
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO`,
`ALERT_EMAIL_FROM`. No new credentials are required. The Actions-provided
`GITHUB_TOKEN` is used only for GitHub API requests. No addresses or credentials
are stored in code, reports, or committed state.

An email contains all new signals from that run, their links, and old/new values.
No change means no email. The first successful page fetch creates a baseline,
not a spurious "page changed" alert. A paper version newer than the known v1,
new implementation candidates, or model candidates can alert on the first run.
The watcher continues after an alert, including after code appears, so a later
weights release is not missed.

## Detection paths

| Source | Trigger / discovery method |
| --- | --- |
| arXiv abstract page, with Atom API fallback | Any version after the stored version; resource-link changes are also reported. **No analysis of why the paper changed is required.** A stale older response cannot roll the baseline back. |
| Official project page | Any change to the fetched HTML, including scripts, styles and media URLs (only CRLF vs LF is ignored). **No release classification is required.** |
| Project source repository | Changes to its Git tree, including media/asset-only changes even when the HTML URL stays unchanged. |
| Author GitHub accounts | Enumerate public repositories owned by `ZZWaang` and `kunfang98927`, including existing repositories. Read new/changed READMEs and repository metadata for the paper ID, title or Music-JEPA spelling variants. An arbitrary repository name is therefore supported. |
| Global GitHub search | Search repository names, descriptions and READMEs for the paper ID, Music-JEPA spellings and full title; also follow repository links from the paper/project page and retain already-discovered candidates. |
| Candidate GitHub repositories | Inspect file trees, code-file hints, checkpoint-file hints, releases/assets and resource links. The existing HTML/audio demo alone is **not** labeled a code release. |
| Hugging Face | Search name variants and the `arxiv:2607.22000` tag; follow model links from the project context; inspect model file listings, revision and gating status. |

Third-party hits are explicitly marked **unconfirmed**. A filename or link is a
hint, not proof of official authorship, complete training/inference code, usable
weights, an open license, or unrestricted downloads. No discovered code is
executed and no weights are downloaded. Gated models are not described as
freely downloadable. Unrelated author repositories do not alert merely because
they are new. Searches can miss resources with no paper/name/author/link clues.

Official starting points:
- https://arxiv.org/abs/2607.22000
- https://zzwaang.github.io/music-jepa-demo/
- https://github.com/ZZWaang/music-jepa-demo

## State, errors and delivery safety

`seen_music_jepa.json` stores per-source snapshots, a README scan cache and error
state. `reports/music_jepa_latest.md` contains the latest report; previous reports
remain available in Git history. Each source establishes its own baseline only
after a successful fetch. An HTTP/API/parser failure never replaces an existing
snapshot with an empty result and does not prevent independent checks.

A newly failing source produces a **technical-error** notification (not a release
claim). A continuous outage is not emailed every day; after recovery a new
outage may notify again. Partial failures remain visible as a failed workflow
**after** sending detected signals and persisting successful checks. Incomplete
search results, truncated Git trees and pagination limits are errors, not clean
"nothing found" results.

The workflow sends email **before committing state**. If SMTP fails, the new
baseline is not pushed and the notification is retried on the next run. As with
the other watchers, a successful email followed by a Git push failure can cause
a duplicate on retry; delivery is at-least-once, not exactly-once. Commits touch
only this watcher's state/report; rebase retries handle other watchers' commits.

## Local tests / dry run

```bash
pip install -r requirements.txt
python -m unittest test_music_jepa_watch.py -v
python music_jepa_watch.py --dry-run --alert-file /tmp/music-jepa-alert.txt
```

The regression tests are offline, mock HTTP, and never send mail. `--dry-run`
leaves state/report files untouched; the optional temporary alert output and
GitHub job summary still show what would be reported. Do not delete the state
file to silence an alert: this resets the baselines and may re-announce existing
candidates.
