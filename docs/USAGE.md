# Usage guide

This guide walks through every way of using `srev`, from a five-second offline run to a full
LLM experiment on your own PostgreSQL database. All commands and outputs shown are real; the
screenshots were produced by [`docs/media/tools/capture.py`](media/tools/capture.py).

- [1. Install](#1-install)
- [2. Five-minute tour (no GPU, no server)](#2-five-minute-tour-no-gpu-no-server)
- [3. Step-by-step pipeline](#3-step-by-step-pipeline)
- [4. Running experiments](#4-running-experiments)
- [5. Using an LLM](#5-using-an-llm)
- [6. Your own database](#6-your-own-database)
- [7. Reading the results](#7-reading-the-results)
- [8. Reproducing a run](#8-reproducing-a-run)
- [8a. Research extensions](#8a-research-extensions)
- [9. Command reference](#9-command-reference)
- [10. Troubleshooting](#10-troubleshooting)

## 1. Install

```bash
git clone https://github.com/mahmudul-islam32/llm-schema-reverse-engineering.git
cd llm-schema-reverse-engineering
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # or ".[postgres]" without the test tools
srev --version
```

Optional services:

| Need | How |
|---|---|
| PostgreSQL 16 | `docker compose up -d postgres` (user/password/db `srev`) |
| vLLM server (GPU) | `docker compose --profile gpu up -d vllm` |
| Any other OpenAI-compatible endpoint | set `SREV_LLM_BASE_URL`, `SREV_LLM_MODEL`, and `SREV_LLM_API_KEY` if it needs a key |
| Metanome | Java + `lib/metanome-cli.jar`, `lib/HyFD.jar`, `lib/HyUCC.jar`, `lib/BINDER.jar` |

Copy `.env.example` to `.env` for the variables used by `docker compose` and the configs.

## 2. Five-minute tour (no GPU, no server)

```bash
srev datasets list
srev run --config configs/offline.yaml
```

The offline config runs three methods (profiling baseline, heuristic, heuristic hybrid) on three
datasets at four noise levels, using SQLite files inside the run directory:

![srev run output](media/experiment-run.png)

Open `runs/<timestamp>_offline/report.md`. The report starts with the databases and their gold
labels, then aggregate tables per constraint type and noise level:

![Rendered run report](media/report-overview.png)

## 3. Step-by-step pipeline

Each pipeline stage is also a command, so you can inspect intermediate files. This is the same
chain as [`examples/quickstart.sh`](../examples/quickstart.sh):

![Step-by-step CLI session](media/cli-walkthrough.png)

| Step | Command | Writes |
|---|---|---|
| Create a constraint-free, noisy working copy and its gold labels | `srev datasets load university --sqlite demo/u.sqlite --noise medium --gold demo/gold.json --workload demo/workload.sql` | SQLite DB, `gold.json`, `workload.sql` |
| Extract evidence | `srev extract --sqlite demo/u.sqlite --name university -o demo/evidence.json` | schema, statistics, samples |
| Mine the query log | `srev qlog demo/workload.sql --evidence demo/evidence.json -o demo/joins.json` | join pairs + failure counts |
| Propose (heuristic or LLM) | `srev propose --evidence … --joins … --heuristic -o demo/proposals.json` | proposals + parse stats |
| Profile (baseline) | `srev profile --sqlite demo/u.sqlite -o demo/baseline.json` | all minimal UCCs/FDs, FK candidates |
| Verify against the data | `srev verify --proposals demo/proposals.json --sqlite demo/u.sqlite -o demo/verified.json` | one verdict per proposal |
| Score against gold | `srev evaluate --proposals demo/verified.json --accepted-only --gold demo/gold.json --evidence demo/evidence.json` | P/R/F1, CIs, hallucinations |
| Synthesise a model | `srev synth --evidence demo/evidence.json --proposals demo/verified.json -o demo/synth` | `model.sql`, `migration.sql`, `model.mmd`, `trace.json` |

`srev evaluate --proposals-are-verified` scores *all* proposals but uses the verdicts for the
hallucination analysis (LLM-only mode); `--accepted-only` scores the verified set (hybrid mode).

## 4. Running experiments

An experiment is a YAML file; [`configs/offline.yaml`](../configs/offline.yaml) and
[`configs/experiment.yaml`](../configs/experiment.yaml) are complete examples.

```yaml
name: my-ablation
seed: 42
database: {backend: postgres, dsn: "${SREV_DSN}"}     # or {backend: sqlite}
datasets:
  - name: tpch_mini
    params: {scale: 1}
  - name: university
noise: [0, 1, 2, 3]                                   # or none/low/medium/high, or explicit levels
methods:
  - {name: profiling, kind: baseline, verify: false}
  - name: llm
    kind: llm
    evidence: [[names], [names, samples], [names, stats, samples, logs]]
    paraphrases: [0, 1, 2]
    seeds: [1, 2, 3]
llm: {base_url: "${SREV_LLM_BASE_URL}", model: Qwen/Qwen2.5-7B-Instruct, temperature: 0.0}
eval: {bootstrap: 1000, top_k_factor: 1.0}
```

```bash
srev run --config my-ablation.yaml                   # runs/<timestamp>_my-ablation/
srev run --config my-ablation.yaml --run-dir runs/x  # fixed directory
```

Recipes:

| Goal | Config |
|---|---|
| RQ2 evidence ablation | several `evidence` combinations on one `llm` method |
| Prompt sensitivity | `paraphrases: [0, 1, 2]` |
| Decoding variance | `seeds: [1, 2, 3]` (temperature stays 0) |
| RQ3 robustness | `noise: [0, 1, 2, 3]`; the report adds noise-curve charts |
| Custom noise | `noise: [{rename_columns: 1.0, nulls: 0.0, typos: 0.0, orphans: 0.0}]` (names only) |
| RQ4 verification | `verify: true` on a method → extra `+verify` variant |
| RQ5 hybrid | `{name: hybrid-llm, kind: hybrid, scorer: llm, threshold: 0.5}` |
| Exact FD gold | `eval: {fd_gold: both}` adds all minimal FDs of the clean data |
| Different verifier strictness | `verify: {fk_min_coverage: 0.9, fd_max_g3: 0.1}` |
| Pick the variant for DDL | `synth: {variant: "llm[names+stats+samples+logs]+verify"}` |

Variant names follow `method[channels]#p<paraphrase>@s<seed>+verify`; the suffixes appear only
when there is more than one paraphrase or seed.

## 5. Using an LLM

1. Start a server. With Docker and a GPU:
   ```bash
   SREV_LLM_MODEL=Qwen/Qwen2.5-7B-Instruct docker compose --profile gpu up -d vllm
   ```
   Or run `vllm serve <model> --seed 42` yourself, or point at any OpenAI-compatible API.
2. Set `SREV_LLM_BASE_URL` (e.g. `http://localhost:8000/v1`) and `SREV_LLM_MODEL`.
3. Pin `llm.revision` in the config to the checkpoint revision you served; it goes into the
   manifest and the cache key.
4. Run `srev run --config configs/experiment.yaml`, or a single proposal:
   ```bash
   srev propose --evidence evidence.json --joins joins.json \
       --base-url "$SREV_LLM_BASE_URL" --model "$SREV_LLM_MODEL" \
       --save-prompt prompt.txt -o proposals.json
   ```

This is the prompt the model receives, built from the evidence with all four channels (shortened
here):

![Prompt sent to the LLM](media/prompt.png)

The model must answer with `{"proposals": [{type, lhs, rhs, confidence, rationale}, …]}`. srev
requests JSON-schema-constrained output (`llm.response_format: json_schema`); servers that do not
support it can use `json_object` or `none`. Output is parsed strictly: invalid JSON counts as a
response failure, and each invalid proposal as an item failure. Both appear in `costs.csv` and the
report. Responses are cached in `llm_cache/` by a hash of the full request.

## 6. Your own database

From a PostgreSQL schema that still has its declared constraints:

```bash
export SREV_DSN=postgresql://user:pass@host:5432/db
srev truth export --schema legacy --name legacy -o gold.json             # declared PK/UNIQUE/FK
srev truth strip  --schema legacy --target legacy_bare                   # copy without constraints
srev extract --schema legacy_bare --name legacy -o evidence.json
srev qlog pg_stat_statements.csv --evidence evidence.json -o joins.json   # optional
srev propose --evidence evidence.json --joins joins.json --heuristic -o proposals.json
srev verify --proposals proposals.json --schema legacy_bare -o verified.json
srev evaluate --proposals verified.json --proposals-are-verified --gold gold.json --evidence evidence.json
srev synth --evidence evidence.json --proposals verified.json --source-schema legacy_bare -o synth/
```

Export a workload with `\copy (SELECT query, calls FROM pg_stat_statements) TO 'pgss.csv' CSV HEADER`.

In a run config, the same database becomes a dataset (noise is then applied on top):

```yaml
datasets:
  - name: legacy
    source: postgres
    dsn: ${LEGACY_DSN}
    schema: legacy
    workload: pgss.csv
    curated_gold: curated.json     # optional extra labels, e.g. undeclared FKs
    max_rows: 200000               # rows read per table
```

A database without declared constraints needs a curated gold file to be scored; without gold you
can still verify proposals and synthesise a model.

## 7. Reading the results

| File | Use it for |
|---|---|
| `report.md` | overview: databases, aggregate and per-database tables, hallucinations, costs, charts |
| `metrics.csv` | one row per database × variant × type with TP/FP/FN, P/R/F1, CIs, top-k and overlap scores |
| `aggregates.csv` | micro/macro averages across databases per noise level, bootstrap CI, p-value vs. the baseline |
| `hallucinations.csv`, `false_positives.jsonl` | FP categories; the JSONL is the input for a manual audit |
| `costs.csv` | seconds, tokens, parse failures, prompt trimming |
| `proposals.jsonl` | every proposal with its verifier statistic |
| `synth/` | DDL, migration script, Mermaid ER diagram and evidence trace |
| `manifest.json` | git SHA, config hash, dataset checksums, model, seeds, hardware |

False positives get exactly one category each:

![Hallucination table](media/report-hallucinations.png)

The synthesiser turns accepted constraints into PostgreSQL DDL. Each constraint names the proposal
it came from, and tables split by BCNF say which FD caused the split:

![Synthesised DDL](media/synth-ddl.png)

The ER diagram (`synth/<db>.mmd`) renders on GitHub and in any Mermaid viewer ([vector version](media/er-diagram.svg)):

![ER diagram](media/er-diagram.png)

For a manual audit, label a sample of `false_positives.jsonl` twice and compute agreement:

```bash
srev audit kappa annotator_a.csv annotator_b.csv      # CSV columns: id,category
```

## 8. Reproducing a run

```bash
srev reproduce runs/<run-id> --replay        # re-run from config.yaml, LLM answers from the cache
srev reproduce runs/<run-id>                 # re-run and call the server again
srev report runs/<run-id>                    # rebuild report.md from the stored tables
```

`reproduce` compares every metric with the original and exits with status 1 if any differs by
more than `--tolerance`.

## 8a. Research extensions

**Approximate profiling.** Exact discovery fails on the first orphan or typo. Error thresholds
make the profiler tolerate dirty data:

```yaml
- name: profiling-approx
  kind: baseline
  profiling: {ucc_max_error: 0.01, fd_max_error: 0.05, ind_min_coverage: 0.9, ind_coverage: rows}
```

`ind_coverage: rows` counts referencing rows instead of distinct values, so a handful of unique
orphan values cannot sink a low-cardinality FK. `srev profile` uses exact discovery.

**Learned FK classifier.** A logistic regression over partial-IND candidates with 14 data,
name and query-log features (after Rostin et al., WebDB 2009). It needs at least two datasets: the
runner trains leave-one-dataset-out, so no noise level of the held-out dataset is ever seen in
training. The report's "Learned FK model" section lists the standardised weights per fold.

```yaml
- {name: learned-fk, kind: learned}
- name: learned-fk-nologs          # ablation: drop the query-log feature
  kind: learned
  learned: {features: [row_coverage, value_coverage, ref_uniqueness, dep_uniqueness,
                       distinct_ratio, name_score, dep_keyish, ref_keyish, ref_first_column,
                       same_table, range_inside, dep_null_ratio, ref_rows_ratio]}
```

**Name-reliance probes (RQ7).** Add probes to the noise list. They keep the data identical and only
change names; the report compares each variant's clean score with every probe:

```yaml
noise: [0, 1, 2, 3, names-abbreviated, names-cryptic, names-misleading]
```

`names-misleading` permutes names among columns and tables, so names point at the wrong data. A
method that drops sharply there leans on names, and on public schemas such as TPC-H possibly on
memory.

**Spider / BIRD.** Download the benchmark yourself (check its licence), then:

```bash
srev datasets spider data/spider                       # list databases with annotated PKs/FKs
```

```yaml
database: {backend: sqlite}
datasets:
  - {name: spider, source: spider, path: data/spider, queries_json: dev.json, limit: 50}
  - {name: bird, source: spider, path: data/bird/dev, tables_json: dev_tables.json,
     db_dir: dev_databases, queries_json: dev.json}
```

**LaTeX tables.**

```bash
srev export latex runs/<run-id> -o tables.tex                       # one table per type, macro F1
srev export latex runs/<run-id> --types FK --ci \
    --variants "profiling,profiling-approx,learned-fk-nologs"       # chosen rows, with CIs
```

## 9. Command reference

| Command | Purpose | Key options |
|---|---|---|
| `srev datasets list` | list built-in generators | |
| `srev datasets load NAME` | generate and load a dataset | `--sqlite/--dsn`, `--scale`, `--seed`, `--noise`, `--with-constraints`, `--gold`, `--workload`, `--noise-log` |
| `srev truth export` | declared constraints → gold JSON | `--schema`, `--name`, `-o` |
| `srev truth strip` | copy a schema without constraints (PostgreSQL) | `--schema`, `--target` |
| `srev extract` | evidence JSON | `--rows`, `--top-k`, `--seed`, `--no-comments` |
| `srev qlog FILE` | join pairs from a workload | `--evidence` |
| `srev propose` | LLM or heuristic proposals | `--heuristic`, `--channels`, `--base-url`, `--model`, `--paraphrase`, `--token-budget`, `--response-format`, `--cache-dir`, `--save-prompt` |
| `srev profile` | profiling baseline | `--ucc-max-arity`, `--fd-max-lhs` |
| `srev verify` | SQL verdicts | `--fk-min-coverage`, `--ucc-min-uniqueness`, `--fd-max-g3` |
| `srev evaluate` | metrics against gold | `--proposals-are-verified`, `--accepted-only`, `--evidence`, `--bootstrap`, `--top-k-factor` |
| `srev synth` | DDL, migration, ER | `--normal-form bcnf/none`, `--source-schema`, `--target-schema` |
| `srev run` | experiment from YAML | `--config`, `--output-dir`, `--run-dir` |
| `srev report RUN` | regenerate report | |
| `srev reproduce RUN` | re-run and compare | `--replay`, `--tolerance`, `-o` |
| `srev audit kappa A B` | Cohen's κ | |
| `srev datasets spider ROOT` | list a Spider/BIRD download | `--tables-json` |
| `srev export latex RUN` | booktabs tables | `--metric`, `--types`, `--variants`, `--ci`, `-o` |

Every database option accepts `--dsn` (PostgreSQL, default `$SREV_DSN`) or `--sqlite PATH`, plus
`--schema` for PostgreSQL. `python -m srev …` works as well as `srev …`.

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `metanome … skipping: Java executable not found` / `JARs not found` | Metanome is optional; install Java and put the JARs under `lib/`, or remove the method |
| `request rejected 400` from the LLM server | the server may not support `json_schema`; set `llm.response_format: json_object` |
| Many `response_failures` | the model ignored the format: check `prompts/` and `llm_cache/`, raise `max_tokens`, or try another model |
| `prompt trimming` column not empty | the prompt exceeded `prompt.token_budget`; raise it or use fewer sample rows |
| `offline mode and no cached response` | `--replay` needs the original `llm_cache/`; run without `--replay` |
| `database.dsn is required for the postgres backend` | set `database.dsn` or `SREV_DSN` |
| PostgreSQL tests skipped | set `SREV_TEST_DSN=postgresql://…` |
| FK recall drops to 0 at higher noise for `profiling` | expected: exact INDs break on a single orphan value; see the robustness charts |
