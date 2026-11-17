# Competition Tracer — 2026-11-17

Letzter Check: 2026-11-17T16:58:15+00:00 · Heute erfasst: 2 · Aus früheren Tagen zur Zustellung übernommen: 0 · Fehler: 1

Tägliche Momentaufnahme; keine vollständige Blockhistorie und keine Ausführung eingereichten Codes.

**Unvollständiger Check: Fehler sind keine Entwarnung.**

## The Innovation Game

Live algorithm submissions, public source branches and protocol status. Daily snapshots are not a replay of every blockchain block.

- **[tig-live](<https://github.com/tig-foundation/tig-monorepo>)** (deep): fehlgeschlagen.
  Fehler: mainnet-api.tig.foundation: retry exceeds daily request budget
- **[tig-papers](<https://arxiv.org>)** (deep): ok.
  {"baseline": false, "cutoff": "2026-11-14T17:12:23.596336Z", "pages": 1, "papers_observed": 0, "seed_papers": 0}

Keine neuen Ereignisse erfasst. Quellenstatus oben beachten.

## Sakana AI — Core War / Digital Red Queen

Competitive assembly-program research framework. No continuously operated public submission leaderboard has been verified.

- **[drq-code](<https://github.com/SakanaAI/drq>)** (standard): ok.
  {"baseline": false, "changes": 0, "commit": "6b2f4459f9dc53f3e533ed5f1263129df71067d7", "depth": "standard", "diffs_attempted": 0, "diffs_omitted_by_limit": 0, "files": 653}
- **[drq-project](<https://pub.sakana.ai/drq/>)** (standard): ok.
  {"baseline": false, "characters": 44337, "embedded_json_blocks": 0, "javascript_evaluated": false, "linked_resources": 18}
- **[core-war-papers](<https://arxiv.org>)** (standard): ok.
  {"baseline": false, "cutoff": "2026-11-14T17:12:26.338638Z", "pages": 1, "papers_observed": 1, "seed_papers": 1}

Keine neuen Ereignisse erfasst. Quellenstatus oben beachten.

## Sakana AI — Sudoku-Bench research and status

The official repository currently marks Sudoku-Bench discontinued; watch for status changes, benchmark updates and new research.

- **[sudoku-repository](<https://github.com/SakanaAI/Sudoku-Bench>)** (deep): ok.
  {"baseline": false, "changes": 0, "commit": "48fd26702ecedbecfe1ffdd3edc91a346c1137e8", "depth": "deep", "diffs_attempted": 0, "diffs_omitted_by_limit": 0, "files": 1}
- **[sudoku-project](<https://pub.sakana.ai/sudoku/>)** (light): ok.
  {"baseline": false, "characters": 8635, "embedded_json_blocks": 0, "javascript_evaluated": false, "linked_resources": 26}
- **[sudoku-papers](<https://arxiv.org>)** (light): ok.
  {"baseline": false, "cutoff": "2026-11-14T17:12:31.753606Z", "pages": 1, "papers_observed": 2, "seed_papers": 1}

### Implicit-Reasoning-Enabled Elastic Semantic Communications for AI-Agent Networks

Typ: paper_published · Quelle: sudoku-papers
Erfasst: 2026-11-17T16:58:15+00:00
Veröffentlicht: 2026-11-16T01:56:50Z
Aktualisiert: 2026-11-16T01:56:50Z

[Originalquelle](<https://arxiv.org/abs/2611.20399v1>)

AI-agent networks introduce new communication demands driven by proactive interactions and autonomous task execution. Existing adaptive semantic communication schemes primarily adjust semantic representations and resource configurations within predefined processing scales and capability sets, which may be insufficient for evolving task requirements. To address this gap, we introduce elastic semantic communications (ESC), a task-driven framework that extends semantic adaptation to the runtime execution and participation of task-supporting capabilities. ESC comprises two complementary dimensions: vertical elasticity adjusts the execution intensity of existing capabilities, while horizontal elasticity reconfigures the participating capability set. The framework connects these dimensions through the expected marginal value of additional information and computation. To realize ESC, we propose an implicit-reasoning-enabled device--edge mechanism that supports variable-depth local inference through shared-parameter state updates and edge-side continuation through wireless transfer of intermediate reasoning states. Experiments on Sudoku-Extreme demonstrate that the benefits of additional inference depend on wireless conditions, while task-aware edge invocation can preserve task accuracy with substantially lower state-transfer overhead.

```json
{
  "paper_id": "2611.20399",
  "version": 1,
  "updated": "2026-11-16T01:56:50Z",
  "authors": [
    "Kaiwen Yu",
    "Gang Wu",
    "Jiancun Fan",
    "Saviour Zammit"
  ],
  "previous_version": null
}
```

## Assembly, program evolution and artificial life

Related research, including Google's Paradigms of Intelligence team; these publications are not Sakana competition entries.

- **[google-poi-publications](<https://github.com/paradigms-of-intelligence/.github>)** (deep): ok.
  {"baseline": false, "changes": 1, "commit": "484b75fa65e00cd7d6ace482f00cad0bef45fa39", "depth": "deep", "diffs_attempted": 1, "diffs_omitted_by_limit": 0, "files": 1}
- **[google-computational-life-code](<https://github.com/paradigms-of-intelligence/cubff>)** (light): ok.
  {"baseline": false, "changes": 0, "commit": "f212e849027c98fcf4b242eccfb5fed435223e23", "depth": "light", "diffs_attempted": 0, "diffs_omitted_by_limit": 0, "files": 60}
- **[assembly-alife-papers](<https://arxiv.org>)** (light): ok.
  {"baseline": false, "cutoff": "2026-11-14T17:12:40.991950Z", "pages": 1, "papers_observed": 4, "seed_papers": 4}

### paradigms-of-intelligence/.github: profile/README.md changed

Typ: code_changed · Quelle: google-poi-publications
Erfasst: 2026-11-17T16:58:15+00:00

[Originalquelle](<https://github.com/paradigms-of-intelligence/.github/blob/484b75fa65e00cd7d6ace482f00cad0bef45fa39/profile/README.md>)

File changed. Static source diff inspected.

```json
{
  "path": "profile/README.md",
  "action": "changed",
  "commit": "484b75fa65e00cd7d6ace482f00cad0bef45fa39",
  "old_sha": "e2f9adc7f01297174a41cf82bc3c870223b64848",
  "new_sha": "ed30c85d0c7f6195b1a5104f81e5f3e1728d30d6",
  "old_size": 41286,
  "new_size": 41737,
  "diff": "--- previous\n+++ current\n@@ -21,6 +21,7 @@\n | Title | Publication |  Author(s) | Publication Date|\n | ---- | ---------- | -------| ----- |\n |[A latent control model for realistic rodent motion](https://arxiv.org/abs/2610.27279) | NeurIPS 2026 | **Aidan Sirbu**, Charles Y. Zhang, Yuanjia Yang, Bence P. Ölveczky, Talmo D. Pereira, **Blake A. Richards**| October 19, 2026|\n+|[Diffusion as a Training Curriculum for Timestep-Free Iterative Reasoning](https://arxiv.org/abs/2609.01449) | NeurIPS 2026 Workshop | **Mariia Drozdova**, **Aidan Sirbu**, **Pietro Miotti**, **Robert Obryk**, **Mayalen Etcheverry**, **Eyvind Niklasson**, **Blake Richards**| October 14, 2026|\n |[Towards a future space-based, highly scalable AI infrastructure system design](https://www.cell.com/joule/fulltext/S2542-4351(26)00362-4)| Joule | **Blaise Aguera y Arcas**, **Travis Beals**, **Maria Biggs,** **Jessica Bloom**, **Thomas Fischbacher**, **Konstantin Gromov**, **Urs Koester**, **Rishiraj Pravahan**, James Manyika | October 1, 2026|\n |[Reasoning with Neural Cellular Automata](https://arxiv.org/abs/2609.36126v1) | ArXiv | **Mayalen Etcheverry**, **Pietro Miotti**, **Aidan Sirbu**, **Konstantin Schürholt**, **Mariia Drozdova**, **Arna Ghosh**, James Manyika, **Blaise Aguera y Arcas**, **Blake Richards**, **Eyvind Niklasson**| September 28, 2026|\n | [Tapes Together Strong: The Co-evolution of Computation and Cooperation](https://arxiv.org/abs/2609.10817)| NeurIPS 2026 |**Kunal Jha**, **Francesco Cicala**, **Blaise Agüera y Arcas**, **Blake Aaron Richards**, Natasha Jaques, Max Kleiman-Weiner, **Eyvind Niklasson**| September 9, 2026|\n@@ -107,6 +108,7 @@\n ## Pi in the Press\n | Title | Publication | Publication Date|\n | ---- | ---------- | -------|\n+| [Baratunde Thurston & Blaise Agüera y Arcas On Interdependence](https://www.youtube.com/watch?v=CpNPzkKy0Fc)| The Long Now Foundation| October 22, 2026|\n | [AI is forcing us to reckon with a 400-year-old problem](https://bigthink.com/philosophy/healing-the-cartesian-wound/) | Big Think | October 21, 2026 |\n | [The next great cultural divide will be over AI “consciousness” ](https://www.youtube.com/watch?v=5JMPE5Cigps) | Intelligence Everywhere | October 14, 2026 |\n | [Google Takes the A.I. Data Center Race to Outer Space](https://www.nytimes.com/2026/09/24/technology/google-suncatcher-ai-data-center-space.html) | New York Times | September 24, 2026 |",
  "diff_truncated": false
}
```

