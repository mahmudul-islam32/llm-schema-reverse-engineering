# Feature Specification: llm-schema-reverse-engineering

All features are **Implemented** and covered by tests. No experiments with real LLMs have been run yet, so no results are claimed. Priority: Must, Should, Could.

| ID | Feature | Priority | Status |
|---|---|---|---|
| F-01 | PostgreSQL schema and sample extractor | Must | Implemented |
| F-02 | Ground-truth builder | Must | Implemented |
| F-03 | Noise injector | Must | Implemented |
| F-04 | Query-log miner | Should | Implemented |
| F-05 | Prompt builder with evidence ablation | Must | Implemented |
| F-06 | LLM proposer (vLLM) with structured output | Must | Implemented |
| F-07 | Metanome baseline runner | Must | Implemented |
| F-08 | SQL verifier (hybrid mode) | Must | Implemented |
| F-09 | Evaluation harness and metrics | Must | Implemented |
| F-10 | Hallucination taxonomy and report | Must | Implemented |
| F-11 | Normalized model and DDL synthesizer | Should | Implemented |
| F-12 | Reproducible run manifests | Must | Implemented |
| F-13 | Approximate dependency discovery | Must | Implemented |
| F-14 | Learned FK classifier (leave-one-dataset-out) | Should | Implemented |
| F-15 | Name-reliance probes | Should | Implemented |
| F-16 | Spider/BIRD benchmark loader | Should | Implemented |
| F-17 | Paper-ready LaTeX export | Could | Implemented |
| F-18 | CSV and DataFrame ingestion, Python API | Should | Implemented |
| F-19 | One-shot discovery (`srev discover`) | Must | Implemented |
| F-20 | dbt project export | Should | Implemented |
| F-21 | Data-quality checks and drift detection | Should | Implemented |
| F-22 | Schema graph analytics and join paths | Could | Implemented |
| F-23 | HTML data-profiling report | Should | Implemented |

---

### F-01: PostgreSQL schema and sample extractor
**Description:** Reads `information_schema` and `pg_catalog` for tables, columns and types, computes column statistics (distinct count, null ratio, min/max, top-k values) and draws seeded row samples.
**User story:** As a researcher, I want consistent, compact evidence per table, so that every method sees the same input.
**Acceptance criteria:** Output is a versioned JSON per database. Sampling is deterministic for a given seed. Declared constraints are excluded from the evidence.
**Priority:** Must · **Status:** Implemented
**Implementation:** `srev.extract` (`srev extract`). Works on PostgreSQL and SQLite. Samples are drawn with a seeded RNG over a canonical row order (PostgreSQL tables above 200k rows are pre-sampled with `TABLESAMPLE BERNOULLI … REPEATABLE`). Only column names, types and comments are read from the catalog.

### F-02: Ground-truth builder
**Description:** Exports declared PKs, FKs and UNIQUE constraints (and curated FDs where available) as gold labels, then drops them from the working copy.
**User story:** As a researcher, I want gold labels taken from real declared constraints, so that metrics are objective.
**Acceptance criteria:** Gold files are stored separately from the evidence. Each label records its source (declared vs curated).
**Priority:** Must · **Status:** Implemented
**Implementation:** `srev.truth` (`srev truth export`, `srev truth strip`). Built-in datasets ship curated FDs/FKs; `curated_gold` adds labels for external databases. Gold files are written to `gold/`, evidence to `evidence/`.

### F-03: Noise injector
**Description:** Configurable perturbations: cryptic column renames (e.g. `customer_id`→`c_k1`), null injection, typos, orphan FK values, and comment removal.
**User story:** As a researcher, I want controlled "legacy-ness", so that I can measure robustness curves.
**Acceptance criteria:** Each perturbation has a level parameter, is logged and is reproducible from the seed.
**Priority:** Must · **Status:** Implemented
**Implementation:** `srev.noise`. Presets L0–L3 or explicit levels; PK/UNIQUE columns are protected from NULLs and typos by default; values never grow beyond their original length; the workload is rewritten with the new names (unparseable statements are dropped and counted).

### F-04: Query-log miner
**Description:** Parses SQL workloads (benchmark query sets or `pg_stat_statements` exports) and extracts equi-join predicates as FK evidence.
**User story:** As a researcher, I want to test whether query usage helps the LLM beyond names and data.
**Acceptance criteria:** Join pairs are extracted with frequencies, and parser failures are counted and reported.
**Priority:** Should · **Status:** Implemented
**Implementation:** `srev.qlog` (`srev qlog`). Reads `.sql`, `pg_stat_statements` CSV (`query`, `calls`) and JSONL; resolves aliases and, given a schema, unqualified columns; counts parse failures and unresolved columns.

### F-05: Prompt builder with evidence ablation
**Description:** Builds prompts from selected evidence channels (names only, plus samples, plus statistics, plus logs) within a token budget.
**User story:** As a researcher, I want to isolate which cues matter.
**Acceptance criteria:** Every prompt is stored with a hash, and channel combinations come from config.
**Priority:** Must · **Status:** Implemented
**Implementation:** `srev.llm.prompt`. Channels `names`, `stats`, `samples`, `logs`; three instruction paraphrases; trimming order samples → top-k → join pairs → stats; prompts saved as `prompts/<sha256>.txt`.

### F-06: LLM proposer (vLLM) with structured output
**Description:** Calls a vLLM OpenAI-compatible server and requests JSON proposals `{type, lhs, rhs, confidence, rationale}`, validated with Pydantic. Malformed outputs are counted, not silently repaired.
**User story:** As a researcher, I want machine-checkable proposals from open models that I can run locally.
**Acceptance criteria:** Model, revision, decoding parameters and seed are logged. The parse-failure rate is reported.
**Priority:** Must · **Status:** Implemented
**Implementation:** `srev.llm.client`, `srev.llm.output`, `srev.llm.proposers`. JSON-schema `response_format`, retries on 5xx only, response cache keyed by request hash (enables offline replay). Response and item failures and stripped code fences are counted.

### F-07: Metanome baseline runner
**Description:** Runs Metanome algorithms on CSV exports: HyFD (FDs), HyUCC (UCCs) and BINDER (unary/n-ary INDs). FK candidates are derived from INDs whose referenced side is a UCC.
**User story:** As a researcher, I want strong, standard statistical baselines.
**Acceptance criteria:** Algorithm versions are pinned, runtime and memory are recorded, and results are converted to the common proposal format.
**Priority:** Must · **Status:** Implemented
**Implementation:** `srev.baselines.profiling` (exact Python profilers, `srev profile`) and `srev.baselines.metanome` (CLI wrapper, CSV export, result parser). The Metanome wrapper is untested against real JARs.

### F-08: SQL verifier (hybrid mode)
**Description:** Validates each proposal on the data: inclusion coverage for FKs, uniqueness for UCCs, and g3-style violation ratio for FDs. Proposals are accepted under configurable thresholds.
**User story:** As a practitioner, I want LLM ideas filtered by evidence, to reduce hallucinations.
**Acceptance criteria:** Each verdict stores the measured statistic. LLM-only, verified-LLM and baseline modes are all evaluated.
**Priority:** Must · **Status:** Implemented
**Implementation:** `srev.verify` (`srev verify`). FK: row-level inclusion coverage (distinct-value coverage stored as detail) plus referenced-side uniqueness; UCC: uniqueness; FD: g3. Unknown columns are rejected as `invalid_reference` before any SQL runs; integer/text mismatches fall back to a text comparison.

### F-09: Evaluation harness and metrics
**Description:** Computes precision, recall and F1 per constraint type (FK, UCC, FD) at exact-match and at column-set-overlap levels, macro- and micro-averaged across databases, with bootstrap confidence intervals.
**User story:** As a reviewer, I want comparable, statistically grounded numbers.
**Acceptance criteria:** Metric definitions match [RESEARCH.md](RESEARCH.md), and tables and plots are generated from the stored results.
**Priority:** Must · **Status:** Implemented
**Implementation:** `srev.eval.metrics` (`srev evaluate`). Exact and overlap (Jaccard ≥ 0.5) matching, top-k cut, item-level bootstrap per database, database-level bootstrap for macro F1, paired sign-flip permutation test.

### F-10: Hallucination taxonomy and report
**Description:** Classifies false positives: non-existent column, contradicted by data, semantically plausible but absent from gold, wrong direction, wrong arity.
**User story:** As a researcher, I want to know *how* LLMs fail, not just how often.
**Acceptance criteria:** Each FP gets exactly one category. Inter-annotator agreement is reported for any manual labels.
**Priority:** Must · **Status:** Implemented
**Implementation:** `srev.eval.hallucination` (`srev audit kappa`). Categories are assigned in a fixed priority order; `false_positives.jsonl` is the input for the manual audit.

### F-11: Normalized model and DDL synthesizer
**Description:** Generates a normalized schema proposal (e.g. BCNF/3NF decomposition from accepted FDs) as PostgreSQL DDL plus a Mermaid ER diagram.
**User story:** As a data engineer migrating a legacy DB, I want a reviewable target model.
**Acceptance criteria:** The DDL parses in PostgreSQL, and every constraint is traceable to its evidence.
**Priority:** Should · **Status:** Implemented
**Implementation:** `srev.synth` (`srev synth`). BCNF decomposition from accepted FDs, PK choice from accepted UCCs, DDL validated with sqlglot and executed in the PostgreSQL tests, topologically ordered migration script, Mermaid ER diagram, `-- evidence:` comments and `trace.json`.

### F-12: Reproducible run manifests
**Description:** Each run writes a manifest with git SHA, config, dataset checksums, model ID and revision, seeds and hardware.
**User story:** As a reviewer, I want to reproduce any reported number.
**Acceptance criteria:** Re-running a manifest with temperature 0 reproduces the metrics, within documented nondeterminism.
**Priority:** Must · **Status:** Implemented
**Implementation:** `srev.manifest`, `srev reproduce [--replay]`. Re-running from the stored config with the cached responses reproduces all metrics exactly in the tests.

### F-13: Approximate dependency discovery
**Description:** Error thresholds turn the exact profilers into approximate ones: UCC error (share of rows to delete for uniqueness), FD g3 error, and partial INDs measured over distinct values or over rows. FK candidates are partial INDs into approximate UCCs.
**User story:** As a researcher, I want a dirty-data baseline, so that LLMs are not only compared against exact discovery, which breaks on the first orphan value.
**Acceptance criteria:** Thresholds of 0 reproduce exact discovery; the measured error is part of every rationale; minimality and key pruning still hold.
**Priority:** Must · **Status:** Implemented
**Implementation:** `srev.baselines.profiling` (`ucc_max_error`, `fd_max_error`, `ind_min_coverage`, `ind_coverage`); `profiling-approx` in both run configs. Pilot: FK macro F1 0.60–0.63 at L0–L3 vs. 0.04–0.65 for exact profiling.

### F-14: Learned FK classifier
**Description:** FK discovery as classification over partial-IND candidates, following Rostin et al. (WebDB 2009): 14 interpretable features (coverage, uniqueness, distinct ratio, name similarity, key-like names, column position, value ranges, NULL ratio, query-log frequency, table sizes), L2-regularised logistic regression in plain Python.
**User story:** As a researcher, I want a learned statistical-plus-semantic baseline and to see which evidence it relies on.
**Acceptance criteria:** Leave-one-dataset-out evaluation (no noise level of the held-out dataset is used in training); feature weights per fold in the report; feature subsets for ablations.
**Priority:** Should · **Status:** Implemented
**Implementation:** `srev.learn`, method kind `learned` (runner collects candidates and verdicts per database, trains after all databases are prepared). Unary FKs only.

### F-15: Name-reliance probes
**Description:** `rename_style` `cryptic`, `abbreviate` (customer_id → cstm_id) or `misleading` (names permuted among columns/tables); presets `names-abbreviated`, `names-cryptic`, `names-misleading` change only names, never data.
**User story:** As a researcher, I want to measure how much a method depends on names or on memorised public schemas.
**Acceptance criteria:** Data is identical to L0; gold labels and the workload are renamed consistently and still hold; the report has a probe table; noise curves keep the ordinal levels only.
**Priority:** Should · **Status:** Implemented
**Implementation:** `srev.noise`, report section "Name-reliance probes".

### F-16: Spider/BIRD benchmark loader
**Description:** `source: spider` expands into one dataset per `db_id` from a Spider- or BIRD-style directory; PK/FK gold labels from `tables.json`; gold SQL as the workload.
**User story:** As a researcher, I want hundreds of annotated databases from different domains for breadth (RQ1).
**Acceptance criteria:** Annotation names are matched case-insensitively; `srev datasets spider ROOT` lists databases with their annotated keys.
**Priority:** Should · **Status:** Implemented
**Implementation:** `srev.datasets.spider`. The benchmark data is not redistributed.

### F-17: Paper-ready LaTeX export
**Description:** `srev export latex RUN_DIR` writes booktabs tables (variants × noise levels and probes) with the best value per column in bold and optional bootstrap CIs.
**User story:** As an author, I want result tables I can paste into a paper without retyping numbers.
**Acceptance criteria:** Tables are generated only from stored aggregates; LaTeX special characters are escaped.
**Priority:** Could · **Status:** Implemented
**Implementation:** `srev.export`.

---

## Data-engineering and data-science features

### F-18: CSV and DataFrame ingestion, Python API
**Description:** `srev ingest csv` loads files or folders into SQLite/PostgreSQL with conservative type inference (zero-padded codes stay text, ISO dates become `DATE`, NULL tokens become NULL); `source: csv` in run configs; `dataset_from_frames` and `srev.api.discover_csv/_frames/_database` for notebooks.
**User story:** As a data scientist, I want to profile the CSV export or DataFrames I already have without setting up a database.
**Acceptance criteria:** Identifiers are sanitised and deduplicated; every inferred type round-trips through SQLite and PostgreSQL.
**Priority:** Should · **Status:** Implemented · **Implementation:** `srev.ingest`, `srev.api`; pandas is an optional extra.

### F-19: One-shot discovery
**Description:** `srev discover` combines approximate profiling, the learned FK classifier (trained on the built-in generators across noise levels and cryptic names, so no labels are needed), selection rules and SQL verification. Output: verified `constraints.json`, `review.json`, `rejected.json`, evidence, DDL, migration, ER diagram; `--html` and `--dbt` add a report and a dbt project.
**User story:** As a data engineer inheriting an undocumented database, I want its keys and relationships in one command, with evidence I can check.
**Acceptance criteria:** Every returned constraint is verified on the data. Accidental inclusions are guarded against: key-to-key FKs with dissimilar names, extra parents of an already-linked column, and weakly named composite FKs go to review. On an unseen HR schema all four FKs and the composite key are found with no false FK; on a deliberately tiny TPC-H sample 9 of 10 FKs are found, plus two accidental small-integer inclusions (a known limitation).
**Priority:** Must · **Status:** Implemented · **Implementation:** `srev.discover`; a new `airline` generator adds self-referencing and parallel FKs to the training data.

### F-20: dbt project export
**Description:** `srev export dbt` / `srev discover --dbt` write `sources.yml` with `unique`, `not_null` and `relationships` tests, singular SQL tests for composite keys, composite FKs and FDs, staging models and `dbt_project.yml`. Approximate constraints get `severity: warn`.
**User story:** As an analytics engineer, I want discovered constraints enforced in my existing dbt pipeline.
**Acceptance criteria:** No extra dbt packages needed; generic-test arguments nested under `arguments:` (dbt 1.10+) with a legacy option. Validated with dbt-core 1.12 + dbt-postgres: all generated tests pass on clean data, approximate ones warn with failing-row counts on dirty data.
**Priority:** Should · **Status:** Implemented · **Implementation:** `srev.dbt`.

### F-21: Data-quality checks and drift detection
**Description:** `srev check` re-measures a constraints file on current data (pass, warn on drift beyond a tolerance, fail, missing column, error) and reports schema drift against baseline evidence (tables/columns added or removed, type and row-count changes). Markdown/JSON output, exit code for CI.
**User story:** As a data engineer, I want to know when a load breaks the structure my downstream jobs rely on.
**Acceptance criteria:** Statistics are compared in the right direction (g3 higher is worse, coverage/uniqueness lower is worse); `--fail-on warn` makes warnings fail the job.
**Priority:** Should · **Status:** Implemented · **Implementation:** `srev.quality`.

### F-22: Schema graph analytics
**Description:** `srev graph` classifies tables (fact, bridge, dimension, lookup, isolated), lists subject areas, hubs and star schemas, and prints the shortest FK join path between two tables as SQL.
**User story:** As an analyst, I want to know how to join two tables in an unfamiliar database.
**Acceptance criteria:** Roles come with a stated reason; join SQL uses the correct column pairs in both directions.
**Priority:** Could · **Status:** Implemented · **Implementation:** `srev.graph`.

### F-23: HTML data-profiling report
**Description:** One self-contained HTML file: summary cards, table roles, ER diagram, per-column profiles (NULL and uniqueness bars, ranges, top values, PK/UK/FK badges), verified constraints and the review list, light and dark theme.
**User story:** As a data scientist, I want an EDA-style overview I can open in a browser or share.
**Acceptance criteria:** All values are HTML-escaped; works without network (the ER diagram then shows its Mermaid source).
**Priority:** Should · **Status:** Implemented · **Implementation:** `srev.htmlreport`.
