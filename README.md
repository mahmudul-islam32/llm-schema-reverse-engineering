# LLM-Assisted Schema Reverse Engineering

![status](https://img.shields.io/badge/status-implemented%2C%20experiments%20pending-yellow)
![license](https://img.shields.io/badge/license-MIT-blue)
![python](https://img.shields.io/badge/python-3.11%2B-informational)
![postgres](https://img.shields.io/badge/PostgreSQL-16-336791)

A research toolkit that asks whether large language models can recover the *implicit* structure of legacy databases: foreign keys (FKs), unique column combinations (UCCs), functional dependencies (FDs) and, from those, a normalized relational model. The LLM gets column names, statistics, sampled rows and query logs. Its proposals are benchmarked against exact data-profiling baselines (and, optionally, the [Metanome](https://hpi.de/naumann/projects/data-profiling-and-analytics/metanome-data-profiling.html) algorithms) on databases whose ground-truth constraints are known. The project extends the author's master's thesis on reverse engineering existing databases into explicit relational models.

> **Status: pipeline implemented, experiments not yet run.** Every feature in [docs/FEATURES.md](docs/FEATURES.md) is implemented and tested (71 tests, including PostgreSQL integration tests). The LLM path has been exercised only against a fake OpenAI-compatible server in the tests; **no experiments with real models have been run and no research results are claimed.**

## Research Question

> Can LLM semantic cues (names, values, query usage) beat purely statistical profiling at inferring keys and dependencies on noisy, undocumented schemas, and where do they hallucinate?

Full protocol: [docs/RESEARCH.md](docs/RESEARCH.md).

## What it does

```mermaid
flowchart LR
    DS[(Dataset<br/>built-in or PostgreSQL)] --> GT[Ground truth<br/>declared + curated]
    DS --> NO[Noise injector<br/>renames, NULLs, typos, orphans]
    NO --> DB[(Working copy<br/>no constraints)]
    NO --> QL[Query-log miner]
    DB --> EX[Extractor<br/>schema, stats, samples]
    EX --> PB[Prompt builder<br/>evidence ablation]
    QL --> PB
    PB --> LLM[vLLM / OpenAI-compatible]
    EX --> HE[Heuristic proposer]
    DB --> BL[Profiling baselines<br/>UCC / FD / IND→FK]
    BL --> HY[Hybrid ranker]
    LLM --> VE[SQL verifier]
    HE --> VE
    HY --> VE
    BL --> EV
    VE --> EV[Evaluator<br/>P/R/F1, CIs, hallucinations]
    GT --> EV
    VE --> SY[DDL + ER synthesizer]
    EV --> RE[Report, manifest]
```

| Feature | Module |
|---|---|
| F-01 PostgreSQL/SQLite schema, statistics and seeded sample extractor | `srev.extract` |
| F-02 Ground truth from declared constraints and curated labels; constraint stripping | `srev.truth` |
| F-03 Seeded noise injector (cryptic renames, NULLs, typos, orphan FKs, comment removal) | `srev.noise` |
| F-04 Query-log miner for `.sql`, `pg_stat_statements` CSV and JSONL workloads | `srev.qlog` |
| F-05 Prompt builder with evidence channels, token budget, paraphrases and prompt hashes | `srev.llm.prompt` |
| F-06 vLLM/OpenAI-compatible proposer with JSON-schema output, strict parsing, response cache | `srev.llm` |
| F-07 Exact profiling baselines (UCC, FD, unary/binary IND→FK) and a Metanome CLI wrapper | `srev.baselines` |
| F-08 SQL verifier: FK inclusion coverage, UCC uniqueness, FD g3 error | `srev.verify` |
| F-09 Precision/recall/F1 per type, exact + overlap match, top-k, bootstrap CIs, paired tests | `srev.eval.metrics` |
| F-10 Hallucination taxonomy and Cohen's κ for manual audits | `srev.eval.hallucination` |
| F-11 BCNF decomposition → PostgreSQL DDL, migration script, Mermaid ER, evidence trace | `srev.synth` |
| F-12 Run manifests (git SHA, config hash, dataset checksums, model, seeds, hardware) and `reproduce` | `srev.manifest`, `srev.cli` |

## Getting Started

Prerequisites: Python 3.11+. Optional: Docker (PostgreSQL 16), an NVIDIA GPU or any OpenAI-compatible endpoint for the LLM methods, Java for Metanome.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

### 1. Offline run (no GPU, no database server)

Runs the profiling baseline, the name/statistics heuristic and heuristic hybrid ranking on all three built-in datasets at four noise levels, using SQLite working copies. Takes a few seconds.

```bash
srev run --config configs/offline.yaml
```

The run directory `runs/<timestamp>_offline/` then contains `report.md`, noise-curve SVGs, metrics tables and all intermediate artefacts (see [Outputs](#outputs)).

### 2. Full protocol with PostgreSQL and vLLM

```bash
docker compose up -d postgres
docker compose --profile gpu up -d vllm          # or point SREV_LLM_BASE_URL at any OpenAI-compatible server
export SREV_DSN=postgresql://srev:srev@localhost:5432/srev
export SREV_LLM_BASE_URL=http://localhost:8000/v1
export SREV_LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
srev run --config configs/experiment.yaml
```

`configs/experiment.yaml` runs the RQ2 evidence ablation (4 channel combinations × 3 prompt paraphrases), LLM-only and verified variants, LLM ranking of baseline candidates (RQ5) and Metanome when its JARs are present. LLM responses are cached by request hash, so a finished run can be replayed offline:

```bash
srev reproduce runs/<run-id> --replay     # exits non-zero if any metric differs
```

### 3. Step by step on your own database

```bash
srev truth export --dsn "$SREV_DSN" --schema legacy -o gold.json          # declared constraints, if any
srev truth strip  --dsn "$SREV_DSN" --schema legacy --target legacy_bare  # copy without constraints
srev extract --dsn "$SREV_DSN" --schema legacy_bare --name legacy -o evidence.json
srev qlog pg_stat_statements.csv --evidence evidence.json -o joins.json
srev propose --evidence evidence.json --joins joins.json \
    --base-url "$SREV_LLM_BASE_URL" --model "$SREV_LLM_MODEL" -o proposals.json
srev verify --proposals proposals.json --dsn "$SREV_DSN" --schema legacy_bare -o verified.json
srev evaluate --proposals verified.json --proposals-are-verified --gold gold.json --evidence evidence.json
srev synth --evidence evidence.json --proposals verified.json --source-schema legacy_bare -o synth/
```

`srev propose --heuristic` runs the offline heuristic instead of an LLM; `srev profile` runs the profiling baseline. [examples/quickstart.sh](examples/quickstart.sh) runs the whole chain on SQLite.

## Methods compared

| Kind | Variant name in reports | What it is |
|---|---|---|
| `baseline` | `profiling` | Exact level-wise discovery of all minimal UCCs (≤ arity 3) and FDs (≤ 2 LHS columns), unary INDs and binary INDs into 2-column UCCs; FK candidates are INDs into UCCs. Ranked by name similarity / FD redundancy for top-k. |
| `metanome` | `metanome` | HyFD, HyUCC and BINDER via `metanome-cli`, results converted to the common format. Skipped with a warning if Java or the JARs are missing. |
| `llm` | `llm[names+stats+…]` | One prompt per evidence combination / paraphrase / seed; JSON-schema-constrained output from a vLLM server. |
| `heuristic` | `heuristic[…]` | Transparent name and statistics rules: the "semantic cues without a model" reference point. Not an LLM. |
| `hybrid` | `hybrid-…` | RQ5: baseline candidates (key-implied FDs removed) scored by an LLM or the heuristic, cut at a threshold. |
| any + verifier | `…+verify` | The same proposals filtered by the SQL verifier (hybrid mode, RQ4). |

## Datasets

| Name | Content | Gold |
|---|---|---|
| `tpch_mini` | Seeded generator with the TPC-H schema (8 tables), not the official `dbgen` | Declared PK/FK (incl. composite FK into `partsupp`) |
| `university` | 8 tables with a self-referencing relationship and denormalised department data | Declared PK/FK/UNIQUE + 4 curated semantic FDs |
| `retail_legacy` | Legacy-style abbreviated names, no declared FKs | Declared PKs/UNIQUE + 6 curated FKs + 2 curated FDs |
| any PostgreSQL / SQLite DB | `source: postgres` or `source: sqlite` in the config | Declared constraints, plus an optional curated gold file |

All generators are deterministic for a given seed and recorded by checksum in the manifest. Real benchmarks (TPC-H `dbgen`, IMDB/JOB, MusicBrainz, Spider/BIRD) are loaded into PostgreSQL first and then used with `source: postgres`; see [docs/RESEARCH.md](docs/RESEARCH.md) §4 for the shortlist and licences.

## Configuration

A run config is YAML validated by Pydantic ([src/srev/config.py](src/srev/config.py)); `${VAR}` and `${VAR:-default}` are expanded from the environment.

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
  manifest.json            git SHA, config hash, dataset checksums, model + revision, seeds, hardware
  config.yaml              resolved config (input for `srev reproduce`)
  report.md                tables, hallucination breakdown, costs, links to charts and DDL
  metrics.{csv,json}       per database × variant × type: TP/FP/FN, P/R/F1 + CIs, top-k, overlap
  aggregates.{csv,json}    micro/macro across databases, bootstrap CI, paired test vs. baseline
  hallucinations.{csv,json}, false_positives.jsonl
  costs.{csv,json}         seconds, tokens, parse failures, prompt trimming
  proposals.jsonl          every proposal with its verifier verdict
  gold/ noise/ evidence/   gold labels (separate from evidence), noise logs, evidence + join stats
  prompts/<sha256>.txt     exact prompt text, addressed by hash
  llm_cache/               raw completions keyed by request hash
  synth/<db>.sql|.mmd|.migration.sql|.trace.json
  noise_curve_{fk,ucc,fd}.svg
```

## Limitations

- The profiling baselines are straightforward exact algorithms in Python, suitable for databases that fit in memory; they are not HyFD/HyUCC/BINDER. The Metanome wrapper follows `metanome-cli`'s documented usage but has only been tested with synthetic result files, not real JARs.
- `tpch_mini` mimics the TPC-H schema with simplified data; it is meant for piloting, not for reporting TPC-H results.
- NULL is treated as a regular value during profiling (NULL = NULL), and the verifier ignores NULLs for FK coverage and UCC uniqueness, as SQL constraints do.
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

## Roadmap

| Milestone | Scope | Status |
|---|---|---|
| M0: Proposal | Research plan, literature review, dataset licences | Plan written; licence checks pending |
| M1: Infrastructure | Loaders, ground truth, noise injector | Implemented |
| M2: Baselines | Profiling baselines, Metanome wrapper, FK derivation from INDs | Implemented; Metanome runs pending |
| M3: LLM pipeline | Prompt builder, vLLM proposer, verifier, ablations | Implemented; no real-model runs yet |
| M4: Evaluation | Full benchmark, hallucination analysis, write-up | Harness implemented; experiments pending |

## Citation

A citation entry will be added once a preprint exists.

## Contributing

Open an issue to discuss a requirement, design decision or feature before you send a pull request. Conventions: feature branches, Conventional Commits, `ruff` + `mypy` + `pytest` in CI.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Mahmudul Islam.

## Author

**Mahmudul Islam** · M.Sc. Computer Science · [github.com/mahmudul-islam32](https://github.com/mahmudul-islam32)
