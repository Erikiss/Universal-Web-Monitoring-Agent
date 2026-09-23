# NeurIPS 2026 — public OpenReview change watch

## Current status: PAUSED, not operational

The code and existing Pushover-mail integration are prepared, but the two live
GitHub Actions tests on **24 September 2026 CEST** could not retrieve any of the
three monitored public sources. Both API queries returned **HTTP 403,
`ChallengeRequiredError`**. Chromium reached **"Verifying your browser"**, not
the conference page. OpenReview's page offers normal account sign-in as an
alternative; no authenticated access route has been configured or tested here.

The 15 offline regression tests passed, and the runner confirmed that the
required existing mail secrets are present. That is NOT an end-to-end delivery
test. **No alert email was sent and no successful baseline was established.**

Scheduled and push-triggered runs are therefore commented out in the workflow
to avoid repeated failed runs. `workflow_dispatch` remains available for manual
diagnostics. Restore the five-minute schedule only after supported source access
has been tested successfully. Do not interpret silence as "papers not released".

Diagnostic runs:
- https://github.com/Erikiss/Universal-Web-Monitoring-Agent/actions/runs/35935224472
- https://github.com/Erikiss/Universal-Web-Monitoring-Agent/actions/runs/35935579119

The source error details are in `reports/neurips_2026_openreview_latest.md`.
Other repository monitors were not modified.

## Intended monitor behavior once access is resolved

Target: https://openreview.net/group?id=NeurIPS.cc/2026/Conference

Workflow: **NeurIPS 2026 OpenReview watch**

Prepared schedule: every five minutes (`2-57/5 * * * *`, UTC), on `main`.
GitHub can delay or drop scheduled runs; five minutes is the configured interval,
not a guaranteed delivery deadline. The monitor has no automatic shutoff after a
hit and no extra user-facing shutoff option.

### What triggers a Pushover mail?

1. A change to the rendered, public conference page: visible text, tabs, or paper
   links. Chromium executes OpenReview's JavaScript; hashing raw HTML would only
   monitor a loading shell. Whitespace and relative-time labels are normalized.
2. A change to publicly readable accepted submissions, queried anonymously with
   `content.venueid=NeurIPS.cc/2026/Conference`.
3. A change to public conference activity, queried anonymously with
   `domain=NeurIPS.cc/2026/Conference`.

API checks compare total counts and the ten most recently modified public notes.
They are availability/change signals, not a full bibliographic export. The page
check covers the initial visible page, not every pagination page. Neither check
uses a personal OpenReview account or private author notifications.

Mail distinguishes **page/activity changed** from **accepted papers publicly
readable**. Only a successful current accepted-submission query with a positive
count produces the second label. This count can grow during rollout: the monitor
does not claim that the entire final list is complete. Poster, spotlight and oral
papers are included through the common accepted venue ID rather than a keyword
filter. A new tab alone is not proof of acceptance.

A first successful run with no public papers initializes a quiet baseline. If
papers are already public on the first successful check, that run alerts
immediately. Identical subsequent snapshots produce no mail. New changes produce
another mail until the workflow is disabled manually.

### Existing delivery path; no new secrets

Uses the existing `send_alert.py` and repository secrets:
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO`,
`ALERT_EMAIL_FROM`. No addresses or credentials are hard-coded. The existing
`ALERT_EMAIL_TO` is reused as the Pushover-mail destination. Startup checks only
verify that required secrets exist; they do not send a setup/test message and do
not themselves prove SMTP or phone delivery.

State is committed only after successful SMTP hand-off, so a failed send is
retried next run. If SMTP succeeds but the subsequent Git push fails, a duplicate
alert is possible on retry; delivery is at-least-once, not exactly-once.

### State and health

- `seen_neurips_2026_openreview.json`: last successful source snapshots, once a
  successful baseline exists.
- `reports/neurips_2026_openreview_latest.md`: last change/baseline/error report.
- Every run writes its check time, source results and errors to the Actions job
  summary. Quiet checks do not generate heartbeat commits; the committed report
  is not a continuously updated heartbeat.
- A failed source is not treated as an empty list or release. The previous good
  snapshot is preserved. Successful independent checks can still alert, followed
  by a red workflow status for any incomplete checks.
- No automatic Pushover outage notifications are sent; source/delivery failures
  appear in GitHub Actions.

### Turn it off manually

Repository → Actions → **NeurIPS 2026 OpenReview watch** → **… → Disable workflow**.
Alternatively delete `.github/workflows/neurips-2026-openreview-watch.yml`.
Other monitors are unaffected.

### Local tests

```bash
python -m unittest test_neurips_openreview_watch -v
```

The regression suite uses fixtures, sends no mail and makes no network requests.
For a real local check install `requirements.txt` plus `playwright==1.55.0`, then
run `python -m playwright install --with-deps chromium` and
`python neurips_openreview_watch.py --alert-file /tmp/neurips-alert.txt`.
The script only prepares the alert; the Actions workflow invokes the existing
SMTP sender. Preserve the same send-before-commit order for manual operation.
