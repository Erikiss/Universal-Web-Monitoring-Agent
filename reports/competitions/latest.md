# Competition Tracer — 2026-11-23

Letzter Check: 2026-11-23T19:22:11+00:00 · Heute erfasst: 2 · Aus früheren Tagen zur Zustellung übernommen: 0 · Fehler: 1

Tägliche Momentaufnahme; keine vollständige Blockhistorie und keine Ausführung eingereichten Codes.

**Unvollständiger Check: Fehler sind keine Entwarnung.**

## The Innovation Game

Live algorithm submissions, public source branches and protocol status. Daily snapshots are not a replay of every blockchain block.

- **[tig-live](<https://github.com/tig-foundation/tig-monorepo>)** (deep): unvollständig.
  {"baseline": false, "baseline_content_uninspected": 537, "block_height": 1440004, "block_id": "2c3dbaedfd69fb4bcd9557f5dae5db4c", "coverage": "daily block snapshot; not an exhaustive block history", "depth": "deep", "pending_sources": 463, "public_branches": 717, "records": 798, "round": 143, "source_checks": 30, "source_coverage": "partial: pending branches are retained for later runs", "source_errors": 0}
  Fehler: TIG source coverage incomplete: 463 branches pending after 30 checks (max_source_checks=30); no pending source snapshot was advanced
- **[tig-papers](<https://arxiv.org>)** (deep): ok.
  {"baseline": false, "cutoff": "2026-11-20T15:30:03.333677Z", "pages": 1, "papers_observed": 0, "seed_papers": 0}

### TIG longhaul2: submitted

Typ: submission · Quelle: tig-live
Erfasst: 2026-11-23T19:22:11+00:00

[Originalquelle](<https://docs.tig.foundation/innovators/code-submission>)

Newly observed submission

```json
{
  "record_kind": "code",
  "algorithm_id": "c002_a136",
  "stage": "submitted",
  "state": {
    "block_confirmed": 1438522,
    "round_submitted": 143,
    "round_pushed": 145,
    "round_active": null,
    "round_merged": null,
    "banned": false
  },
  "details": {
    "name": "longhaul2",
    "player_id": "0x1842ee174950117b871828a68e5be09d305ad31c",
    "challenge_id": "c002",
    "algorithm_id": "c002_a110",
    "fee_paid": "10000000000000000000"
  },
  "adoption": null,
  "compile_success": null,
  "block_id": "2c3dbaedfd69fb4bcd9557f5dae5db4c",
  "height": 1440004,
  "public": false
}
```

### TIG sat_exact_sp128: merged

Typ: submission · Quelle: tig-live
Erfasst: 2026-11-23T19:22:11+00:00

[Originalquelle](<https://github.com/tig-foundation/tig-monorepo/tree/satisfiability/sat_exact_sp128/tig-algorithms/>)

Adoption: 0.00% → 7.67%

```json
{
  "record_kind": "code",
  "algorithm_id": "c001_a127",
  "stage": "merged",
  "state": {
    "block_confirmed": 1357555,
    "round_submitted": 135,
    "round_pushed": 137,
    "round_active": 139,
    "round_merged": 140,
    "banned": false
  },
  "details": {
    "name": "sat_exact_sp128",
    "player_id": "0x003551a316597a1ea553f3498d246499ef76905c",
    "challenge_id": "c001",
    "algorithm_id": null,
    "fee_paid": "10000000000000000000"
  },
  "adoption": 0.07666121876308081,
  "compile_success": true,
  "block_id": "2c3dbaedfd69fb4bcd9557f5dae5db4c",
  "height": 1440004,
  "public": true
}
```

## Sakana AI — Core War / Digital Red Queen

Competitive assembly-program research framework. No continuously operated public submission leaderboard has been verified.

- **[drq-code](<https://github.com/SakanaAI/drq>)** (standard): ok.
  {"baseline": false, "changes": 0, "commit": "6b2f4459f9dc53f3e533ed5f1263129df71067d7", "depth": "standard", "diffs_attempted": 0, "diffs_omitted_by_limit": 0, "files": 653}
- **[drq-project](<https://pub.sakana.ai/drq/>)** (standard): ok.
  {"baseline": false, "characters": 44337, "embedded_json_blocks": 0, "javascript_evaluated": false, "linked_resources": 18}
- **[core-war-papers](<https://arxiv.org>)** (standard): ok.
  {"baseline": false, "cutoff": "2026-11-20T15:30:05.609631Z", "pages": 1, "papers_observed": 1, "seed_papers": 1}

Keine neuen Ereignisse erfasst. Quellenstatus oben beachten.

## Sakana AI — Sudoku-Bench research and status

The official repository currently marks Sudoku-Bench discontinued; watch for status changes, benchmark updates and new research.

- **[sudoku-repository](<https://github.com/SakanaAI/Sudoku-Bench>)** (deep): ok.
  {"baseline": false, "changes": 0, "commit": "48fd26702ecedbecfe1ffdd3edc91a346c1137e8", "depth": "deep", "diffs_attempted": 0, "diffs_omitted_by_limit": 0, "files": 1}
- **[sudoku-project](<https://pub.sakana.ai/sudoku/>)** (light): ok.
  {"baseline": false, "characters": 8635, "embedded_json_blocks": 0, "javascript_evaluated": false, "linked_resources": 26}
- **[sudoku-papers](<https://arxiv.org>)** (light): ok.
  {"baseline": false, "cutoff": "2026-11-20T15:30:11.183275Z", "pages": 1, "papers_observed": 1, "seed_papers": 1}

Keine neuen Ereignisse erfasst. Quellenstatus oben beachten.

## Assembly, program evolution and artificial life

Related research, including Google's Paradigms of Intelligence team; these publications are not Sakana competition entries.

- **[google-poi-publications](<https://github.com/paradigms-of-intelligence/.github>)** (deep): ok.
  {"baseline": false, "changes": 0, "commit": "484b75fa65e00cd7d6ace482f00cad0bef45fa39", "depth": "deep", "diffs_attempted": 0, "diffs_omitted_by_limit": 0, "files": 1}
- **[google-computational-life-code](<https://github.com/paradigms-of-intelligence/cubff>)** (light): ok.
  {"baseline": false, "changes": 0, "commit": "f212e849027c98fcf4b242eccfb5fed435223e23", "depth": "light", "diffs_attempted": 0, "diffs_omitted_by_limit": 0, "files": 60}
- **[assembly-alife-papers](<https://arxiv.org>)** (light): ok.
  {"baseline": false, "cutoff": "2026-11-20T15:30:20.819718Z", "pages": 1, "papers_observed": 4, "seed_papers": 4}

Keine neuen Ereignisse erfasst. Quellenstatus oben beachten.

