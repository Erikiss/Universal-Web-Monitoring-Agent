# Competition Tracer — 2026-11-12

Letzter Check: 2026-11-12T13:38:20+00:00 · Heute erfasst: 2 · Aus früheren Tagen zur Zustellung übernommen: 0 · Fehler: 0

Tägliche Momentaufnahme; keine vollständige Blockhistorie und keine Ausführung eingereichten Codes.

## The Innovation Game

Live algorithm submissions, public source branches and protocol status. Daily snapshots are not a replay of every blockchain block.

- **[tig-live](<https://github.com/tig-foundation/tig-monorepo>)** (deep): Baseline angelegt.
  {"baseline": true, "baseline_content_uninspected": 717, "block_height": 1424157, "block_id": "60e406a765b41a6fcfafd619f5fcc9d3", "coverage": "daily block snapshot; not an exhaustive block history", "depth": "deep", "pending_sources": 0, "public_branches": 717, "records": 778, "round": 142, "source_checks": 0, "source_coverage": "complete", "source_errors": 0}
- **[tig-papers](<https://arxiv.org>)** (deep): Baseline angelegt.
  {"baseline": true, "cutoff": "2026-11-05T13:38:28.552844Z", "pages": 1, "papers_observed": 0, "seed_papers": 0}

Keine neuen Ereignisse erfasst. Quellenstatus oben beachten.

## Sakana AI — Core War / Digital Red Queen

Competitive assembly-program research framework. No continuously operated public submission leaderboard has been verified.

- **[drq-code](<https://github.com/SakanaAI/drq>)** (standard): Baseline angelegt.
  {"baseline": true, "commit": "6b2f4459f9dc53f3e533ed5f1263129df71067d7", "depth": "standard", "files": 653}
- **[drq-project](<https://pub.sakana.ai/drq/>)** (standard): Baseline angelegt.
  {"baseline": true, "characters": 44337, "embedded_json_blocks": 0, "javascript_evaluated": false, "linked_resources": 18}
- **[core-war-papers](<https://arxiv.org>)** (standard): Baseline angelegt.
  {"baseline": true, "cutoff": "2026-11-05T13:38:31.169324Z", "pages": 1, "papers_observed": 1, "seed_papers": 1}

Keine neuen Ereignisse erfasst. Quellenstatus oben beachten.

## Sakana AI — Sudoku-Bench research and status

The official repository currently marks Sudoku-Bench discontinued; watch for status changes, benchmark updates and new research.

- **[sudoku-repository](<https://github.com/SakanaAI/Sudoku-Bench>)** (deep): Baseline angelegt.
  {"baseline": true, "commit": "48fd26702ecedbecfe1ffdd3edc91a346c1137e8", "depth": "deep", "files": 1}
- **[sudoku-project](<https://pub.sakana.ai/sudoku/>)** (light): Baseline angelegt.
  {"baseline": true, "characters": 8635, "embedded_json_blocks": 0, "javascript_evaluated": false, "linked_resources": 26}
- **[sudoku-papers](<https://arxiv.org>)** (light): Baseline angelegt.
  {"baseline": true, "cutoff": "2026-11-05T13:38:35.834670Z", "pages": 1, "papers_observed": 8, "seed_papers": 1}

### Energy-Based Reasoners: Your Recurrent Reasoner Should Be an Energy-Based Model

Typ: paper_published · Quelle: sudoku-papers
Erfasst: 2026-11-12T13:38:20+00:00
Veröffentlicht: 2026-11-06T04:40:47Z
Aktualisiert: 2026-11-06T04:40:47Z

[Originalquelle](<https://arxiv.org/abs/2611.08977v1>)

Looping is a powerful inductive bias for reasoning. By repeatedly iterating a recurrent block, looped models can scale their effective depth and enable multi-step reasoning in latent space. However, existing looped models are not inherently guided by an explicit measure of progress, making it difficult to determine when to stop looping and how to improve upon the current state. To enable this, we introduce Energy-Based Reasoners (EBRs), which learn an energy function over latent reasoning states and formulate looped updates as gradient descent on this energy. The energy scores latent states, while its gradient guides refinement and provides a signal for stopping. To train EBRs effectively, we further introduce exploration-based training to discover additional modes and to better shape the energy landscape over a broader set of states. Across four algorithmic reasoning benchmarks, EBRs match or outperform prior recurrent reasoning models, achieving 99.9% accuracy on Sudoku-Extreme while using 345$\times$ less inference compute than the next-best model and maintaining comparable training compute. On all multi-solution benchmarks, EBRs achieve state-of-the-art coverage of distinct valid solutions while maintaining near-100% single-sample accuracy. These results suggest that energy-based models are a promising approach for strong reasoning performance.

```json
{
  "paper_id": "2611.08977",
  "version": 1,
  "updated": "2026-11-06T04:40:47Z",
  "authors": [
    "William Xingxu Chen",
    "Alexi Gladstone",
    "Zeyu Michael Li",
    "Heng Ji",
    "Yilun Du"
  ],
  "previous_version": null
}
```

### Where to Approximate in Neurosymbolic Inference?

Typ: paper_published · Quelle: sudoku-papers
Erfasst: 2026-11-12T13:38:20+00:00
Veröffentlicht: 2026-11-10T10:22:37Z
Aktualisiert: 2026-11-10T10:22:37Z

[Originalquelle](<https://arxiv.org/abs/2611.13862v1>)

Probabilistic neurosymbolic methods rely on weighted model counting (WMC) to combine neural predictors with symbolic constraints. Exact WMC is #P-hard and typically scales poorly, so many methods resort to approximations. By unifying existing approaches under a three-step bottom-up approximate compilation pipeline, we study where approximation budget is best allocated. Inspired by tensor networks, we instantiate two steps with tensor train decompositions and derive new pipelines that exactly multiply approximated factors and recompress their products via SVD- or interpolation-based schemes. Across six tasks in Sudoku, arithmetic reasoning, and scene understanding, and across increasing problem sizes, our pipelines produce WMC estimates orders-of-magnitude more accurate than prior methods. This translates into stronger downstream learning on tasks where reasoning relies on tight interactions between symbolic factors, where cruder approximations ignore these dependencies. Our pipelines open novel design space for effective approximate compilation of challenging probabilistic reasoning tasks.

```json
{
  "paper_id": "2611.13862",
  "version": 1,
  "updated": "2026-11-10T10:22:37Z",
  "authors": [
    "Samy Badreddine",
    "Emile van Krieken",
    "Luciano Serafini",
    "Antonio Vergari"
  ],
  "previous_version": null
}
```

## Assembly, program evolution and artificial life

Related research, including Google's Paradigms of Intelligence team; these publications are not Sakana competition entries.

- **[google-poi-publications](<https://github.com/paradigms-of-intelligence/.github>)** (deep): Baseline angelegt.
  {"baseline": true, "commit": "d70b1a3acbc184e4440b15ba71bd92a3201a4f1d", "depth": "deep", "files": 1}
- **[google-computational-life-code](<https://github.com/paradigms-of-intelligence/cubff>)** (light): Baseline angelegt.
  {"baseline": true, "commit": "f212e849027c98fcf4b242eccfb5fed435223e23", "depth": "light", "files": 60}
- **[assembly-alife-papers](<https://arxiv.org>)** (light): Baseline angelegt.
  {"baseline": true, "cutoff": "2026-11-05T13:38:42.878425Z", "pages": 1, "papers_observed": 4, "seed_papers": 4}

Keine neuen Ereignisse erfasst. Quellenstatus oben beachten.

