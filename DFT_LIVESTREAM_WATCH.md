# Downtown First Thursdays: video-stream announcements

Added at the user's request on 2026-10-01. This is a separate monitor; all existing research monitors and their schedules are unchanged.

## Schedule and notification

- Every 15 minutes, at minute 07, 22, 37 and 52, throughout the **first Thursday of each month in America/Los_Angeles** (00:00-23:59). The event is monthly, not weekly.
- A UTC Thursday/Friday envelope plus an IANA timezone gate handles both PDT and PST. Other Thursdays only run the cheap calendar gate. In Berlin the event evening falls on Friday morning. This is an ongoing schedule, not limited to the October 2026 edition.
- The next 2026 event days are October 1, November 5 and December 3. The regular 17:00-22:00 SF event window is 02:00-07:00 the following morning in Berlin on these three dates. Monitoring starts earlier to catch announcements.
- GitHub Actions schedules can be delayed or dropped; **15 minutes is a requested cadence, not a delivery SLA**. Public-repository schedules can also be disabled after 60 days without repository activity.
- Alerts use the **existing** `send_alert.py`, `SMTP_*`, `ALERT_EMAIL_TO` and optional `ALERT_EMAIL_FROM` secrets. No address or credential is committed, printed, or changed. Secret values cannot be inspected through the connector; the initial run checks their presence, not actual SMTP delivery.
- A new, dated video-stream announcement generates an email with the viewing URL, public source and evidence. No routine "nothing found" email and no unsolicited test email.
- Deduplication is per San Francisco event date and canonical stream URL. A failed SMTP send does not mark the link delivered. If Git persistence fails after successful SMTP, the next check can repeat the notification; exactly-once email delivery cannot be guaranteed.

## Sources and honest coverage limits

The four starting pages are:

1. https://www.dftsf.com/
2. https://civicjoyfund.org/projects/dft
3. https://downtownsf.org/do/downtown-first-thursdays-1
4. https://111minnagallery.com/

The watcher also follows a bounded number of same-host DFT-related links, with at most 12 pages and a four-minute source-check budget per run. It uses the public HTML and clearly identifies itself. It observes robots.txt and crawl delays; it never logs in, rotates IP addresses, bypasses challenges, or treats a refusal as a successful empty check.

**This is not an all-web or all-social-media search engine.** It can find links to YouTube, Twitch, Instagram Live, TikTok Live, Facebook Live and other explicit watch/stream pages when the monitored organizers/partners publish them. It cannot reliably see app-only Instagram/TikTok lives, ephemeral stories, private posts, or unlinked third-party streams. There is no search-provider API credential configured for this new monitor.

Detection is deliberately conservative: the source must concern DFT, the nearby text must explicitly describe a video livestream, the URL must be a plausible viewing link, and the announcement or event-page heading must match the current event date. Ordinary "live music", KALW radio/audio broadcasts, generic social icons, old dated videos and obvious replays do not trigger. Undated announcements may be missed. A dated announcement is labeled as a **hint/announcement**, not a verified playing video.

Unreadable/blocked sources produce a red workflow and a diagnostic summary. Valid new announcements from other readable sources may still be delivered; the email discloses incomplete checks. Error runs do not send a separate recurring failure email.

## Files and diagnostics

- `.github/workflows/dft-livestream-watch.yml`: schedule, calendar gate, secrets, tests, mail and post-delivery persistence.
- `dft_livestream_watch.py`: public-page reader and dated-announcement detector.
- `test_dft_livestream_watch.py`: offline regression tests, including Berlin's Friday / Pacific Thursday boundary, DST, first Thursday on day 7, duplicates, radio/recap rejection and robots refusals.
- `seen_dft_livestreams.json`: created only after a successfully delivered alert; retains the latest 12 event dates.
- `reports/dft_livestream_latest.md`: last delivered alert, without private mail configuration.

A push changing watcher code runs a **dry run**, not an email test. In Actions, **DFT livestream watch -> Run workflow** defaults to `dry_run=true`. A non-dry manual run still refuses to send outside the first Pacific Thursday. No source access or SMTP success should be claimed before examining actual Actions logs.

Offline tests:

```sh
python -m unittest test_dft_livestream_watch -v
```

Sources for the calendar and scheduling caveats:
- https://www.dftsf.com/
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
