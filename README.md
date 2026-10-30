<div align="center">

# LLM-Assisted Schema Reverse Engineering

**Recover the keys, foreign keys and functional dependencies that a legacy database never declared, and measure whether LLMs do it better than statistical profiling.**

![status](https://img.shields.io/badge/status-implemented%2C%20experiments%20pending-yellow)
![license](https://img.shields.io/badge/license-MIT-blue)
![python](https://img.shields.io/badge/python-3.11%2B-informational)
![postgres](https://img.shields.io/badge/PostgreSQL-16-336791)
![vLLM](https://img.shields.io/badge/LLM-vLLM%20%7C%20OpenAI--compatible-6d28d9)
![tests](https://img.shields.io/badge/tests-71%20passing-15803d)

[Demo](#demo) · [Architecture](#architecture) · [Quick start](#quick-start) · [Usage guide](docs/USAGE.md) · [Screenshots](#screenshots) · [Research plan](docs/RESEARCH.md)

<img src="docs/media/demo.gif" alt="Terminal demo: srev loads a noisy database, extracts evidence, proposes, verifies, evaluates and synthesises a schema, then runs a full experiment" width="820">

<sub>Real output replayed from <code>docs/media/tools/capture.py</code> · <a href="docs/media/demo.mp4">MP4 version</a></sub>

</div>

---

`srev` asks whether large language models can recover the *implicit* structure of legacy databases: foreign keys (FKs), unique column combinations (UCCs), functional dependencies (FDs) and, from those, a normalised relational model. The LLM gets column names, statistics, sampled rows and query logs. Its proposals are checked against the data with SQL and benchmarked against exact data-profiling baselines (and, optionally, the [Metanome](https://hpi.de/naumann/projects/data-profiling-and-analytics/metanome-data-profiling.html) algorithms) on databases whose ground-truth constraints are known. The project extends the author's master's thesis on reverse engineering existing databases into explicit relational models.

> [!IMPORTANT]
> **Status: pipeline implemented, experiments not yet run.** Every feature in [docs/FEATURES.md](docs/FEATURES.md) is implemented and tested (71 tests, including PostgreSQL integration tests). The LLM path has only run against a fake OpenAI-compatible server in the tests. **No experiments with real models have been run and no research results are claimed.** The numbers in the screenshots come from the offline baseline and heuristic methods on generated data.

## Contents

- [Research question](#research-question)
- [Demo](#demo)
- [Architecture](#architecture)
- [How a run works](#how-a-run-works)
- [Quick start](#quick-start)
- [Screenshots](#screenshots)
- [Methods and evaluation](#methods-and-evaluation)
- [Datasets](#datasets)
- [Configuration](#configuration)
- [Outputs](#outputs)
- [Limitations](#limitations)
- [Development](#development)
- [Roadmap](#roadmap)

## Research question

> Can LLM semantic cues (names, values, query usage) beat purely statistical profiling at inferring keys and dependencies on noisy, undocumented schemas, and where do they hallucinate?

| | Question | What srev measures |
|---|---|---|
| **RQ1** | LLM-only vs. statistical profiling | P / R / F1 per FK, UCC and FD, exact and overlap match |
| **RQ2** | Which evidence matters? | ablation over `names`, `stats`, `samples`, `logs` |
| **RQ3** | Robustness to legacy noise | noise levels L0–L3 → robustness curves |
| **RQ4** | Hallucinations, and what verification removes | taxonomy of false positives, `+verify` variants |
| **RQ5** | Hybrid: profiler candidates ranked by an LLM | `hybrid` method |

Full protocol: [docs/RESEARCH.md](docs/RESEARCH.md).

## Demo

The animation above replays a real session. Every stage of the pipeline is a command:

```bash
srev datasets load university --sqlite demo/u.sqlite --noise medium --gold demo/gold.json --workload demo/workload.sql
srev extract  --sqlite demo/u.sqlite --name university -o demo/evidence.json
srev qlog     demo/workload.sql --evidence demo/evidence.json -o demo/joins.json
srev propose  --evidence demo/evidence.json --joins demo/joins.json --heuristic -o demo/proposals.json   # or --base-url/--model for an LLM
srev verify   --proposals demo/proposals.json --sqlite demo/u.sqlite -o demo/verified.json
srev evaluate --proposals demo/verified.json --accepted-only --gold demo/gold.json --evidence demo/evidence.json
srev synth    --evidence demo/evidence.json --proposals demo/verified.json -o demo/synth
```

…and `srev run --config <file>.yaml` runs the whole matrix of datasets × noise levels × methods.

## Architecture

![srev architecture](docs/media/architecture.svg)

| # | Component | Module | Feature |
|---|---|---|---|
| 1 | Ground truth from declared constraints and curated labels; constraint stripping | `srev.truth` | F-02 |
| 2 | Seeded noise: cryptic renames, NULLs, typos, orphan FKs, comment removal | `srev.noise` | F-03 |
| 3 | Working copy without any constraints (PostgreSQL schema or SQLite file) | `srev.db` | — |
| 4 | Schema, statistics and seeded samples → versioned evidence JSON | `srev.extract` | F-01 |
| 5 | Equi-join pairs from `.sql`, `pg_stat_statements` CSV, JSONL | `srev.qlog` | F-04 |
| 6 | Exact UCC / FD / IND discovery, FK = IND into UCC; Metanome wrapper | `srev.baselines` | F-07 |
| 7 | Prompt builder with evidence ablation; vLLM client; strict JSON parsing | `srev.llm` | F-05, F-06 |
| 8 | SQL verifier: FK coverage, UCC uniqueness, FD g3 error | `srev.verify` | F-08 |
| 9 | Metrics, bootstrap CIs, permutation test, hallucination taxonomy | `srev.eval` | F-09, F-10 |
| 10 | BCNF decomposition → DDL, migration script, Mermaid ER, evidence trace | `srev.synth` | F-11 |
| — | Run manifests, reports, `reproduce` | `srev.manifest`, `srev.report`, `srev.cli` | F-12 |

Design details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## How a run works

![What srev run does](docs/media/pipeline.svg)

The life of a single LLM proposal, from evidence to a score:

```mermaid
sequenceDiagram
    autonumber
    participant R as Runner
    participant P as Prompt builder
    participant V as vLLM server
    participant C as Response cache
    participant Q as Working copy (SQL)
    participant E as Evaluator
    R->>P: evidence + channels (names, stats, samples, logs)
    P->>P: trim to token budget, hash prompt (SHA-256)
    P->>C: lookup request hash
    alt cache miss
        P->>V: POST /v1/chat/completions (JSON schema, T=0, seed)
        V-->>P: {"proposals": [...]}
        P->>C: store completion
    end
    P->>R: valid proposals + counted parse failures
    R->>Q: coverage / uniqueness / g3 per proposal
    Q-->>R: verdict with the measured statistic
    R->>E: proposals, verdicts, gold labels
    E-->>R: P/R/F1 + CIs, hallucination categories, top-k
```

How the verifier decides on a proposal:

```mermaid
flowchart LR
    P[Proposal] --> X{columns exist?}
    X -- no --> INV[invalid_reference<br/>rejected]
    X -- yes --> T{type}
    T -- FK --> FK["row coverage ≥ 0.95<br/>and referenced side unique ≥ 0.99"]
    T -- UCC --> U["distinct ÷ non-NULL rows ≥ 0.99"]
    T -- FD --> F["g3 error ≤ 0.05"]
    FK --> D{pass?}
    U --> D
    F --> D
    D -- yes --> OK[accepted<br/>kept in +verify variant]
    D -- no --> REJ[rejected<br/>→ contradicted_by_data]
```

What the LLM gets to see is controlled per run:

![Evidence channels](docs/media/evidence-channels.svg)

## Quick start

```bash
git clone https://github.com/mahmudul-islam32/llm-schema-reverse-engineering.git
cd llm-schema-reverse-engineering
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

**1. Offline, in seconds (no GPU, no database server).** Profiling baseline, heuristic and heuristic hybrid on three datasets × four noise levels, with SQLite working copies:

```bash
srev run --config configs/offline.yaml
# then open runs/<timestamp>_offline/report.md in any Markdown viewer
```

**2. Full protocol with PostgreSQL and an LLM.**

```bash
docker compose up -d postgres
docker compose --profile gpu up -d vllm      # or any OpenAI-compatible endpoint
export SREV_DSN=postgresql://srev:srev@localhost:5432/srev
export SREV_LLM_BASE_URL=http://localhost:8000/v1
export SREV_LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
srev run --config configs/experiment.yaml
srev reproduce runs/<run-id> --replay        # replays cached LLM answers, compares every metric
```

**3. Your own database.** `srev truth export` → `srev truth strip` → `srev extract` → `srev propose` → `srev verify` → `srev evaluate` → `srev synth`. See [docs/USAGE.md §6](docs/USAGE.md#6-your-own-database).

📘 **The [usage guide](docs/USAGE.md)** covers every command, config recipes for each research question, LLM setup, reading the results and troubleshooting.

## Screenshots

All screenshots show real output; how they were made is described in [Regenerating the media](#regenerating-the-media).

<table>
<tr>
<td width="50%"><b>Step-by-step CLI pipeline</b><br><a href="docs/media/cli-walkthrough.png"><img src="docs/media/cli-walkthrough.png" alt="CLI walkthrough"></a></td>
<td width="50%"><b>Running an experiment</b><br><a href="docs/media/experiment-run.png"><img src="docs/media/experiment-run.png" alt="srev run output"></a></td>
</tr>
<tr>
<td><b>Run report: databases, aggregate scores, robustness</b><br><a href="docs/media/report-overview.png"><img src="docs/media/report-overview.png" alt="Report overview"></a></td>
<td><b>Hallucination breakdown per variant</b><br><a href="docs/media/report-hallucinations.png"><img src="docs/media/report-hallucinations.png" alt="Hallucination table"></a></td>
</tr>
<tr>
<td><b>The prompt an LLM receives</b><br><a href="docs/media/prompt.png"><img src="docs/media/prompt.png" alt="LLM prompt"></a></td>
<td><b>Synthesised DDL with evidence comments</b><br><a href="docs/media/synth-ddl.png"><img src="docs/media/synth-ddl.png" alt="Synthesised DDL"></a></td>
</tr>
<tr>
<td colspan="2"><b>Synthesised ER diagram</b> (university database, verified heuristic-hybrid proposals; <code>course_dept_id</code> was split off by BCNF)<br><a href="docs/media/er-diagram.png"><img src="docs/media/er-diagram.png" alt="ER diagram" width="720"></a></td>
</tr>
</table>

Robustness chart written by the report (FK macro F1 against noise level, offline run):

<img src="docs/media/sample-noise-curve-fk.svg" alt="FK macro F1 by noise level" width="640">

## Methods and evaluation

![Methods and evaluation modes](docs/media/evaluation-modes.svg)

| Kind | Variant in reports | What it is |
|---|---|---|
| `baseline` | `profiling` | Exact level-wise discovery of all minimal UCCs (≤ arity 3) and FDs (≤ 2 LHS columns), unary INDs and binary INDs into 2-column UCCs; FK candidates are INDs into UCCs. Ranked by name similarity / FD redundancy for top-k. |
| `metanome` | `metanome` | HyFD, HyUCC and BINDER via `metanome-cli`, results converted to the common format. Skipped with a warning if Java or the JARs are missing. |
| `llm` | `llm[names+stats+…]` | One prompt per evidence combination / paraphrase / seed; JSON-schema-constrained output from a vLLM server. |
| `heuristic` | `heuristic[…]` | Transparent name and statistics rules: "semantic cues without a model". Not an LLM. |
| `hybrid` | `hybrid-…` | RQ5: baseline candidates (key-implied FDs removed) scored by an LLM or the heuristic, cut at a threshold. |
| any + verifier | `…+verify` | The same proposals filtered by the SQL verifier (hybrid mode, RQ4). |

Every false positive gets exactly one category:

![Hallucination taxonomy](docs/media/hallucination-taxonomy.svg)

Noise levels for the robustness curves (values read from `srev.noise.PRESETS`):

![Noise presets](docs/media/noise-levels.svg)

## Datasets

| Name | Content | Gold labels |
|---|---|---|
| `tpch_mini` | Seeded generator with the TPC-H schema (8 tables), not the official `dbgen` | Declared PK/FK, including the composite FK into `partsupp` |
| `university` | 8 tables with a self-referencing relationship and denormalised department data | Declared PK/FK/UNIQUE + 4 curated semantic FDs |
| `retail_legacy` | Legacy-style abbreviated names, no declared FKs | Declared PKs/UNIQUE + 6 curated FKs + 2 curated FDs |
| any PostgreSQL / SQLite DB | `source: postgres` or `source: sqlite` in the config | Declared constraints, plus an optional curated gold file |

```mermaid
erDiagram
    tbl_cust }o--|| lkp_region : "region_cd (curated)"
    tbl_ord }o--|| tbl_cust : "cust_no (curated)"
    tbl_ord }o--|| lkp_region : "ship_region_cd (curated)"
    tbl_ord_line }o--|| tbl_ord : "ord_no (curated)"
    tbl_ord_line }o--|| tbl_prod : "prod_no (curated)"
    tbl_ship }o--|| tbl_ord : "ord_no (curated)"
```
<sub>Gold relationships of <code>retail_legacy</code>: none of them is declared in the database, which is the situation the project targets.</sub>

All generators are deterministic for a given seed and recorded by checksum in the manifest. Real benchmarks (TPC-H `dbgen`, IMDB/JOB, MusicBrainz, Spider/BIRD) are loaded into PostgreSQL first and then used with `source: postgres`; see [docs/RESEARCH.md](docs/RESEARCH.md) §4.

## Configuration

A run config is YAML validated by Pydantic ([src/srev/config.py](src/srev/config.py)); `${VAR}` and `${VAR:-default}` are expanded from the environment. More recipes in [docs/USAGE.md §4](docs/USAGE.md#4-running-experiments).

| Key | Example | Description |
|---|---|---|
| `database.backend` | `sqlite` / `postgres` | Where working copies live (`database.dsn` for PostgreSQL) |
| `datasets` | `[{name: tpch_mini, params: {scale: 1}}]` | Built-in generators or `source: postgres` databases, optional `workload` and `curated_gold` |
| `noise` | `[0, 1, 2, 3]` | Presets L0–L3 (`none`, `low`, `medium`, `high`) or explicit perturbation levels |
| `methods[].kind` | `llm`, `heuristic`, `baseline`, `metanome`, `hybrid` | Methods to run; names must be unique |
| `methods[].evidence` | `[[names], [names, samples]]` | Evidence channel combinations for the ablation |
| `methods[].paraphrases` / `seeds` | `[0, 1, 2]` / `[1, 2, 3]` | Prompt wording variants / decoding seeds |
| `llm.base_url`, `llm.model`, `llm.revision` | `http://localhost:8000/v1` | Endpoint and pinned checkpoint |
| `llm.temperature`, `llm.seed` | `0.0`, `42` | Decoding parameters (recorded in the manifest) |
| `llm.response_format` | `json_schema` | `json_schema`, `json_object` or `none` |
| `llm.cache_dir` | `llm_cache` | Response cache shared across runs (default: inside the run directory) |
| `sample.rows_per_table`, `sample.top_k` | `50`, `5` | Evidence extraction |
| `prompt.token_budget` | `12000` | Prompts are trimmed (samples first) to fit |
| `verify.*` | `fk_min_coverage: 0.95` | Acceptance thresholds for FK coverage, UCC uniqueness, FD g3 |
| `eval.bootstrap`, `eval.top_k_factor`, `eval.fd_gold` | `1000`, `1.0`, `curated` | CIs, top-k cut, FD gold source (`curated`, `exact`, `both`) |
| `synth.variant`, `synth.normal_form` | `llm[names]+verify`, `bcnf` | Which variant feeds the DDL synthesiser |
| `seed` | `42` | Global seed for data, noise and sampling |

## Outputs

```
runs/<timestamp>_<name>/
├── manifest.json            git SHA, config hash, dataset checksums, model + revision, seeds, hardware
├── config.yaml              resolved config (input for `srev reproduce`)
├── report.md                tables, hallucination breakdown, costs, charts, links to DDL
├── metrics.{csv,json}       database × variant × type: TP/FP/FN, P/R/F1 + CIs, top-k, overlap
├── aggregates.{csv,json}    micro/macro across databases, bootstrap CI, paired test vs. baseline
├── hallucinations.{csv,json}, false_positives.jsonl
├── costs.{csv,json}         seconds, tokens, parse failures, prompt trimming
├── proposals.jsonl          every proposal with its verifier verdict
├── gold/ noise/ evidence/   gold labels (kept apart from evidence), noise logs, evidence + joins
├── prompts/<sha256>.txt     exact prompt text, addressed by hash
├── llm_cache/               raw completions keyed by request hash
├── synth/<db>.sql | .migration.sql | .mmd | .trace.json
└── noise_curve_{fk,ucc,fd}.svg
```

## Limitations

- The profiling baselines are straightforward exact algorithms in Python for databases that fit in memory; they are not HyFD/HyUCC/BINDER. The Metanome wrapper follows `metanome-cli`'s documented usage but has only been tested with synthetic result files, not real JARs.
- `tpch_mini` mimics the TPC-H schema with simplified data; it is meant for piloting, not for reporting TPC-H results.
- NULL is treated as a regular value during profiling (NULL = NULL); the verifier ignores NULLs for FK coverage and UCC uniqueness, as SQL constraints do.
- The token estimate used for prompt budgeting is a character heuristic, not the model's tokenizer.
- Results depend on the model, its revision and the serving stack; the manifest records them but cannot remove that dependency.

## Development

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy
pytest                                    # PostgreSQL tests are skipped unless SREV_TEST_DSN is set
SREV_TEST_DSN=postgresql://srev:srev@localhost:5432/srev pytest
```

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs lint and type checks, the test suite on Python 3.11 and 3.12 against PostgreSQL 16, an offline smoke run (uploaded as an artifact) and a Docker build.

### Regenerating the media

Infographics are generated from the package's own constants; screenshots and the video come from a real session rendered with Playwright (Chromium) and ffmpeg:

```bash
python docs/media/tools/infographics.py                       # docs/media/*.svg diagrams
python docs/media/tools/capture.py --build /tmp/srev-media --mermaid path/to/mermaid.min.js
node docs/media/tools/render.mjs /tmp/srev-media docs/media    # PNG screenshots, demo.mp4, demo.gif
```

## Roadmap

| Milestone | Scope | Status |
|---|---|---|
| M0: Proposal | Research plan, literature review, dataset licences | Plan written; licence checks pending |
| M1: Infrastructure | Loaders, ground truth, noise injector | ✅ Implemented |
| M2: Baselines | Profiling baselines, Metanome wrapper, FK derivation from INDs | ✅ Implemented; Metanome runs pending |
| M3: LLM pipeline | Prompt builder, vLLM proposer, verifier, ablations | ✅ Implemented; no real-model runs yet |
| M4: Evaluation | Full benchmark, hallucination analysis, write-up | Harness implemented; experiments pending |

## Citation

A citation entry will be added once a preprint exists.

## Contributing

Open an issue to discuss a requirement, design decision or feature before you send a pull request. Conventions: feature branches, Conventional Commits, `ruff` + `mypy` + `pytest` in CI.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Mahmudul Islam.

## Author

**Mahmudul Islam** · M.Sc. Computer Science · [github.com/mahmudul-islam32](https://github.com/mahmudul-islam32)
