# Competition Tracer

The Competition Tracer checks several algorithm competitions and related research sources each day. Its configuration separates a competition or research track from its sources, so another competition can be added without changing the runner.

The daily report includes source coverage, new or changed submissions, code changes, new papers or paper versions, and any failed or incomplete checks. It uses the existing SMTP/Pushover email route and needs no new paid service.

## Included tracks

| Track | Default depth | What is checked |
| --- | --- | --- |
| The Innovation Game (TIG) | `deep` | A pinned mainnet block, submission states, public submission branches, source fingerprints and bounded text excerpts/diffs; related arXiv papers. |
| Sakana AI Core War / Digital Red Queen | `standard` | Changes to the official research code and project page; new Core War/Redcode papers and versions of the DRQ paper. |
| Sakana AI Sudoku-Bench | `light`, with repository `deep` | Repository/status changes with bounded text differences, the public project page, and new Sudoku benchmark/reasoning papers. |
| Assembly, program evolution and artificial life | `light`, with publication list `deep` | Google's Paradigms of Intelligence publication-list differences and Computational Life code, plus related arXiv papers. |

Status checked when adding these sources on 12 November 2026:

- **TIG is the live protocol/algorithm-submission track.** Public source code does not necessarily change every blockchain block. Submissions have their own branches, and publication can be delayed by protocol rounds.
- **DRQ is a research framework based on the competitive Core War game.** A continuously operated public Sakana submission leaderboard has not been verified. The report does not treat every research commit as a tournament result.
- **Sakana's Sudoku-Bench README currently marks the project discontinued.** The tracer watches its status and related research; it does not describe the benchmark as an active submission contest. The repository remains useful for detecting a revival or a new official resource.
- The Google team is named **Paradigms of Intelligence**. Its assembly/artificial-life work is related research, not Sakana competition entries.

## Verified starting sources

| Source | URL |
| --- | --- |
| TIG source and submission branches | https://github.com/tig-foundation/tig-monorepo |
| TIG mainnet API | https://mainnet-api.tig.foundation |
| TIG submission structure | https://docs.tig.foundation/innovating/code-submission |
| TIG advance submissions | https://docs.tig.foundation/innovating/advance-submission |
| DRQ code | https://github.com/SakanaAI/drq |
| DRQ project | https://pub.sakana.ai/drq/ |
| DRQ paper | https://arxiv.org/abs/2601.03335 |
| Sudoku-Bench repository/status | https://github.com/SakanaAI/Sudoku-Bench |
| Sudoku project | https://pub.sakana.ai/sudoku/ |
| Sudoku-Bench paper | https://arxiv.org/abs/2505.16135 |
| Paradigms of Intelligence publications | https://github.com/paradigms-of-intelligence/.github/blob/main/profile/README.md |
| Computational Life code | https://github.com/paradigms-of-intelligence/cubff |

The assembly research source also tracks versions of these initial papers:

- [Computational Life](https://arxiv.org/abs/2406.19108)
- [Coevolution of self-replication and function in a digital primordial soup](https://arxiv.org/abs/2607.09211)
- [BFF: Simple explanations for complex phenomena](https://arxiv.org/abs/2607.01483)
- [Tapes Together Strong](https://arxiv.org/abs/2609.10817)

## TIG depth and coverage

Monitoring `main` alone would miss public submissions. TIG stores an algorithm on a branch named `<challenge>/<algorithm>`, with files under `tig-algorithms/src/<challenge>/<algorithm>/`. Advance proposals use `tig-algorithms/advances/<challenge>/<advance>.md`. `main` contains merged work.

The TIG collector reads a consistent block ID and paginates the public branch list. It compares submission metadata separately from source changes. This matters because a new monorepo commit is not automatically a new version of a participant's algorithm.

The first TIG run records the full public branch/SHA inventory as its baseline. It does not queue years of existing algorithms for deep inspection. Subsequent new or changed branches receive the bounded source checks below; an unfinished batch remains pending for later runs.

Deep source checks have explicit budgets in `competition_sources.json`:

| Setting | Default example | Purpose |
| --- | --- | --- |
| `max_branch_pages` | `50` | Up to 100 GitHub branches per page; reaching an incomplete listing must be visible. |
| `max_source_checks` | `30` | Detailed checks of changed submission branches per run. Remaining work stays pending. |
| `max_source_files` | `100` | Per-submission source-file limit. |
| `max_content_files` | `4` | Maximum files whose text is inspected for a deep check. |
| `max_file_bytes` | `262144` | Limit for downloaded text content (256 KiB). |
| `max_diff_lines` | `60` | Bound on displayed text differences. |
| `adoption_delta` | `0.05` | Meaningful adoption-change threshold, expressed as a fraction (five percentage points). |

These bounds keep daily runs practical. The report distinguishes incomplete source coverage from a quiet day. Text excerpts are not a full semantic assessment. The tracer never executes participant code, measures its performance, or claims that a code change improves a benchmark.

A daily snapshot cannot reconstruct every intermediate block, briefly existing branch or intra-day source version. The captured block and source revisions identify what was actually observed.

## Daily schedule and delivery

`.github/workflows/competition-tracer.yml` runs daily at **07:23 UTC** (**08:23 CET / 09:23 CEST**). GitHub Actions schedules may start late. A push changing the tracer on `main` also checks it; pull requests run the offline tests without sending mail.

Production runs are serialized and check out the latest `main` after acquiring their execution slot, so a queued run sees the previous run's saved delivery acknowledgement. Tests continue to use their event's commit.

The workflow uses the existing repository secrets documented in [README.md](README.md): `SMTP_HOST`, optional `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` and optional `ALERT_EMAIL_FROM`. GitHub access uses the workflow's built-in `github.token`.

One digest is prepared for each UTC day, including quiet days so source health is visible. A persisted delivery acknowledgement prevents a normal rerun from sending the same day's digest again. Later same-day findings remain in the cumulative report and are queued for the next day's digest. Queued findings also appear in that next day's full report, labeled with their original detection time.

Delivery order is deliberate:

1. Check sources and write cumulative reports and candidate state.
2. Send the pending digest through `send_alert.py`.
3. Acknowledge successful delivery.
4. Commit this tracer's state and reports, rebasing unrelated monitor updates if needed.
5. Mark the workflow failed if any checks were incomplete.

If SMTP fails, the workflow does not consume the pending delivery or commit its candidate state. Reports are retained as workflow artifacts. SMTP and Git cannot form one transaction: a failure after SMTP accepts the message but before the acknowledgement is persisted can cause a retry to deliver it again. No exactly-once delivery guarantee is claimed.

## State and reports

- `seen_competitions.json`: comparison state, source checkpoints and delivery state.
- `reports/competitions/`: daily cumulative JSON/Markdown reports and `latest.md`.
- Workflow artifacts retain generated reports for 30 days, including delivery/persistence failures.

The first successful GitHub/page observation establishes a baseline rather than labeling the entire history as new. arXiv discovery starts with the configured recent lookback (`initial_lookback_days: 7`) and also watches explicitly listed seed papers for later versions. Subsequent observations are deduplicated by stable source identities and revisions.

Failed sources retain their previous successful comparison state. A failed or truncated source is listed as incomplete, never silently interpreted as "no changes". Successful independent sources can still contribute to the report.

The page collector checks text available in the fetched HTML. The Sudoku leaderboard uses JavaScript; ranking changes loaded from external data are **not guaranteed** to appear in this page check. Repository and paper tracking remain independent. To track such a leaderboard exactly, add its verified data endpoint as a dedicated collector instead of claiming that HTML changes cover it.

## Extend the tracer

Edit `competition_sources.json`. Each competition has a unique `id`, a display `name`, a description, a default `depth`, and a `sources` list. Every source must have a globally unique `id` and a supported `type`. Set `enabled: false` to disable a source without deleting its configuration.

Changing a source's identity or selection filter establishes a new comparison baseline, so unrelated content is not compared across repositories, paths or queries. Operational adjustments such as depth and request/detail limits preserve its existing cursor and pending work. The Sudoku repository and Google's publication README currently opt into `deep` with `max_diff_files: 5`, giving useful status/publication text differences while keeping those checks small.

| Source type | Main configuration fields | Use |
| --- | --- | --- |
| `tig` | `repo`, `api_url`, depth and budgets above | TIG protocol/submission tracking. |
| `github` | `repo`, `branch`, `paths` | Repository files; an empty `paths` list covers the repository. |
| `page` | `url`, optional `selector` | Official project/news text. |
| `arxiv` | `query`, optional `seed_ids`, `initial_lookback_days` | New papers and later versions. |

Each source can override its competition's depth. TIG uses `light` for metadata-only checks, `standard` for source fingerprints, and `deep` for text detail. Generic GitHub sources detect matching file-SHA changes at every depth; `deep` additionally requests bounded text differences. Page and paper collectors use their own content/query limits rather than pretending a depth setting evaluates JavaScript or reads an entire paper.

Example additional track:

```json
{
  "id": "another-competition",
  "name": "Another competition",
  "description": "Official implementation and related papers",
  "depth": "standard",
  "sources": [
    {
      "id": "another-competition-code",
      "type": "github",
      "repo": "owner/repository",
      "branch": "main",
      "paths": ["README.md", "submissions/"]
    },
    {
      "id": "another-competition-papers",
      "type": "arxiv",
      "query": "all:\"Exact Competition Name\"",
      "initial_lookback_days": 7
    }
  ]
}
```

Use narrow paper queries. The TIG example deliberately avoids the standalone acronym `TIG`, which matches unrelated research. Papers discovered by a query are relevant candidates, not automatically official results or endorsements.

## Run and test

From the repository root:

```bash
pip install -r requirements.txt
python -m unittest discover -p 'test_competition_*.py' -v
python competition_tracer.py --dry-run --alert-file /tmp/competition-preview.txt
```

Use **Actions → Competition Tracer → Run workflow → dry_run** to preview the real sources without sending email or writing any files. The preview appears in stdout and the GitHub Actions job summary; no report artifact or alert file is created in this mode. A real workflow run needs `contents: write` permission to save its reports. Branch rules that reject the bot's commit produce a visible failure; the persistence script never force-pushes or bypasses those rules.
