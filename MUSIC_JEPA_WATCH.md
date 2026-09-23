# Music-JEPA release watch

Daily monitoring for **Music-JEPA: Learning a World Model of Sound from Action**
(arXiv **2607.22000**). The Goodfire and Anthropic monitors, their schedules/state,
and the shared `send_alert.py` are unchanged.

## Notifications: separate origins, separate cadences

The workflow `.github/workflows/music-jepa-release-watch.yml` runs daily at
**07:37 UTC** (09:37 CEST / 08:37 CET). GitHub Actions can start late.
It uses `music_jepa_delivery.py`; `music_jepa_watch.py` remains the collector.
Manual `dry_run` and `test_email` inputs are retained. Implementation changes on
`main` also trigger a run, without bypassing the weekly delivery gate.

| Category | What it means | Notification |
| --- | --- | --- |
| **A. KLARE TREFFER / DIREKTE QUELLENÄNDERUNG** | A new arXiv version, changed paper resource links, changed official project HTML, or changed files in the official demo website repository. These are directly observed source changes, NOT generic search matches. | In the daily run when a change is detected. Any new paper version or page change is sufficient; no release interpretation is required. |
| **B. AUTOREN-/PROJEKTHINWEIS** | A relevant repository in an observed author account, or a resource linked from a monitored paper/project/author source. Origin is printed explicitly. This is NOT automatically a confirmed official release. | In the daily run, in a separate section from A. |
| **D. WOCHENSAMMLUNG: DRITTANBIETER** | Unconfirmed GitHub or Hugging Face search/discovery matches. A related title/README, code filename or checkpoint filename does not establish official authorship. | Collected daily but emailed separately, no more often than once per seven days, and only when there are unreported findings/changes. |
| **C. TECHNISCHE STÖRUNG** | A source could not be checked. This is neither a release nor a research finding. | Separate technical section/subject; continuous outages are not emailed every day. |

Daily and weekly messages have **different subjects and bodies**:

```text
[Web-Monitor] Music-JEPA | DIREKTE QUELLENÄNDERUNG (1)
[Web-Monitor] Music-JEPA | AUTOREN-/PROJEKTHINWEIS (1)
[Web-Monitor] Music-JEPA | WOCHENSAMMLUNG: DRITTANBIETER (3)
```

The numbers above are illustrative. A daily email never embeds third-party
findings, even if a paper change and third-party discoveries occur together.
When both a daily alert and a weekly digest are due, they are separate emails.
The report displays the status of the paper, project page and demo source
separately: changed, no new change detected, or not completely checked.

Mail content describes **what changed** and **where the hint came from**, rather
than dumping long `code_files`, hashes or empty JSON arrays. Code-file counts,
a few example filenames, checkpoint hints, access gating and relevant links
remain visible. A clear source change still does not prove code AND weights
have been released.

## Weekly queue and migration

`seen_music_jepa.json` retains all previous observations and adds a `delivery`
section with `pending`, `categories`, `next_digest_at`, and `last_digest_at`.
Existing unchanged hits—including the four initial third-party notifications—
are NOT reannounced merely because this delivery format was introduced.

New third-party events are persisted every day, even when no email is sent.
Changes are grouped by repository/model, with first/last collection timestamps.
Unchanged snapshots are not added again. The first weekly digest is eligible
seven days after delivery-state initialization. Subsequent digests are eligible
at least seven days after the previous one, at the next actual run. This is a
rolling seven-day gate, not a separate fixed-weekday cron. A quiet interval
produces no empty email; a missed run does not discard the queue.

A direct paper/page change always bypasses the third-party queue. If a previously
unconfirmed resource becomes linked from a monitored primary/author source,
it can move to the daily author/project category without waiting for a digest;
its queued third-party entry is removed. Links appearing only in third-party
READMEs never gain primary provenance merely by being discovered.

The monitor continues after an alert, so code appearing before weights does not
stop later monitoring. The first successful fetch of a new page establishes a
baseline, not a false page-change alarm.

## Detection paths

| Source | Checks |
| --- | --- |
| arXiv abstract page / Atom fallback | Version progression and resource-link changes. Stale older responses cannot roll back the version baseline. |
| Official project page | Changes to fetched HTML, including scripts, styles and media URLs; only CRLF vs LF is ignored. |
| Official demo repository | Git-tree changes, including media/asset-only changes. The existing HTML/audio demo alone is not a code release. |
| Author accounts | Public repositories of `ZZWaang` and `kunfang98927`, including existing repositories and arbitrary future names. Read changed metadata/READMEs for paper ID/title/name matches. |
| Global GitHub search | Paper ID, title and name variants; follow project links and retain discovered candidates. Inspect file trees, releases/assets and resource links. |
| Hugging Face | Name variants, arXiv tag and discovered model links; inspect file listings, revisions and gating. Unknown provenance stays in the third-party digest. |

No discovered code is executed and no weights are downloaded. Filenames or
links do not prove completeness, usable weights, an open license or unrestricted
downloads. Gated models are not described as freely downloadable. Searches can
miss resources lacking all name/paper/author/link clues.

Starting points:
- https://arxiv.org/abs/2607.22000
- https://zzwaang.github.io/music-jepa-demo/
- https://github.com/ZZWaang/music-jepa-demo

## Delivery safety and errors

Email uses the existing `send_alert.py` and repository secrets: `SMTP_HOST`,
`SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO`,
`ALERT_EMAIL_FROM`. No new credentials or recipients are introduced.
The Actions-provided `GITHUB_TOKEN` is used for GitHub API requests only.

The workflow sends both due emails **before pushing state**. If SMTP fails,
new snapshots and a cleared digest queue are not committed, allowing retry.
As before, a successful email followed by a failed Git push (or failure of the
second email) can cause a duplicate on retry. Delivery is at-least-once, not
exactly-once; the seven-day gate prevents ordinary repeated digest sends, not
all possible duplicates caused by delivery/persistence failures.

Each source establishes a baseline only after successful fetching. Failed or
incomplete results do not replace valid snapshots with empty data. Independent
checks continue. Partial errors mark the workflow failed after notifications
and successful state persistence. Continuous errors remain visible in the
report without daily repeat mail.

`reports/music_jepa_latest.md` is the latest categorized report. Earlier reports
remain in Git history. Commits from the workflow touch only its state/report;
rebase retries accommodate other watchers' commits.

## Offline tests and dry run

```bash
pip install -r requirements.txt
python -m unittest discover -p 'test_music_jepa*.py' -v
python music_jepa_delivery.py --dry-run \
  --alert-file /tmp/music-jepa-daily.txt \
  --weekly-alert-file /tmp/music-jepa-weekly.txt
```

Tests mock network access and never send mail. They cover direct/daily signals,
author provenance, third-party isolation, the seven-day boundary, empty weeks,
persisted queues, deduplication, migration, missed runs, promotion, dry runs and
independent simultaneous daily/weekly deliveries. `--dry-run` leaves state and
report files untouched; temporary mail previews and the job summary may be
written. Do not delete state to silence a notification: it resets baselines.
