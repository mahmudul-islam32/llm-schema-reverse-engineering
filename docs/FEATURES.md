# Feature Specification: llm-schema-reverse-engineering

All features are **Planned**. Priority: Must, Should, Could.

| ID | Feature | Priority | Status |
|---|---|---|---|
| F-01 | PostgreSQL schema and sample extractor | Must | Planned |
| F-02 | Ground-truth builder | Must | Planned |
| F-03 | Noise injector | Must | Planned |
| F-04 | Query-log miner | Should | Planned |
| F-05 | Prompt builder with evidence ablation | Must | Planned |
| F-06 | LLM proposer (vLLM) with structured output | Must | Planned |
| F-07 | Metanome baseline runner | Must | Planned |
| F-08 | SQL verifier (hybrid mode) | Must | Planned |
| F-09 | Evaluation harness and metrics | Must | Planned |
| F-10 | Hallucination taxonomy and report | Must | Planned |
| F-11 | Normalized model and DDL synthesizer | Should | Planned |
| F-12 | Reproducible run manifests | Must | Planned |

---

### F-01: PostgreSQL schema and sample extractor
**Description:** Reads `information_schema` and `pg_catalog` for tables, columns and types, computes column statistics (distinct count, null ratio, min/max, top-k values) and draws seeded row samples.
**User story:** As a researcher, I want consistent, compact evidence per table, so that every method sees the same input.
**Acceptance criteria:** Output is a versioned JSON per database. Sampling is deterministic for a given seed. Declared constraints are excluded from the evidence.
**Priority:** Must · **Status:** Planned

### F-02: Ground-truth builder
**Description:** Exports declared PKs, FKs and UNIQUE constraints (and curated FDs where available) as gold labels, then drops them from the working copy.
**User story:** As a researcher, I want gold labels taken from real declared constraints, so that metrics are objective.
**Acceptance criteria:** Gold files are stored separately from the evidence. Each label records its source (declared vs curated).
**Priority:** Must · **Status:** Planned

### F-03: Noise injector
**Description:** Configurable perturbations: cryptic column renames (e.g. `customer_id`→`c_k1`), null injection, typos, orphan FK values, and comment removal.
**User story:** As a researcher, I want controlled "legacy-ness", so that I can measure robustness curves.
**Acceptance criteria:** Each perturbation has a level parameter, is logged and is reproducible from the seed.
**Priority:** Must · **Status:** Planned

### F-04: Query-log miner
**Description:** Parses SQL workloads (benchmark query sets or `pg_stat_statements` exports) and extracts equi-join predicates as FK evidence.
**User story:** As a researcher, I want to test whether query usage helps the LLM beyond names and data.
**Acceptance criteria:** Join pairs are extracted with frequencies, and parser failures are counted and reported.
**Priority:** Should · **Status:** Planned

### F-05: Prompt builder with evidence ablation
**Description:** Builds prompts from selected evidence channels (names only, plus samples, plus statistics, plus logs) within a token budget.
**User story:** As a researcher, I want to isolate which cues matter.
**Acceptance criteria:** Every prompt is stored with a hash, and channel combinations come from config.
**Priority:** Must · **Status:** Planned

### F-06: LLM proposer (vLLM) with structured output
**Description:** Calls a vLLM OpenAI-compatible server and requests JSON proposals `{type, lhs, rhs, confidence, rationale}`, validated with Pydantic. Malformed outputs are counted, not silently repaired.
**User story:** As a researcher, I want machine-checkable proposals from open models that I can run locally.
**Acceptance criteria:** Model, revision, decoding parameters and seed are logged. The parse-failure rate is reported.
**Priority:** Must · **Status:** Planned

### F-07: Metanome baseline runner
**Description:** Runs Metanome algorithms on CSV exports: HyFD (FDs), HyUCC (UCCs) and BINDER (unary/n-ary INDs). FK candidates are derived from INDs whose referenced side is a UCC.
**User story:** As a researcher, I want strong, standard statistical baselines.
**Acceptance criteria:** Algorithm versions are pinned, runtime and memory are recorded, and results are converted to the common proposal format.
**Priority:** Must · **Status:** Planned

### F-08: SQL verifier (hybrid mode)
**Description:** Validates each proposal on the data: inclusion coverage for FKs, uniqueness for UCCs, and g3-style violation ratio for FDs. Proposals are accepted under configurable thresholds.
**User story:** As a practitioner, I want LLM ideas filtered by evidence, to reduce hallucinations.
**Acceptance criteria:** Each verdict stores the measured statistic. LLM-only, verified-LLM and baseline modes are all evaluated.
**Priority:** Must · **Status:** Planned

### F-09: Evaluation harness and metrics
**Description:** Computes precision, recall and F1 per constraint type (FK, UCC, FD) at exact-match and at column-set-overlap levels, macro- and micro-averaged across databases, with bootstrap confidence intervals.
**User story:** As a reviewer, I want comparable, statistically grounded numbers.
**Acceptance criteria:** Metric definitions match [RESEARCH.md](RESEARCH.md), and tables and plots are generated from the stored results.
**Priority:** Must · **Status:** Planned

### F-10: Hallucination taxonomy and report
**Description:** Classifies false positives: non-existent column, contradicted by data, semantically plausible but absent from gold, wrong direction, wrong arity.
**User story:** As a researcher, I want to know *how* LLMs fail, not just how often.
**Acceptance criteria:** Each FP gets exactly one category. Inter-annotator agreement is reported for any manual labels.
**Priority:** Must · **Status:** Planned

### F-11: Normalized model and DDL synthesizer
**Description:** Generates a normalized schema proposal (e.g. BCNF/3NF decomposition from accepted FDs) as PostgreSQL DDL plus a Mermaid ER diagram.
**User story:** As a data engineer migrating a legacy DB, I want a reviewable target model.
**Acceptance criteria:** The DDL parses in PostgreSQL, and every constraint is traceable to its evidence.
**Priority:** Should · **Status:** Planned

### F-12: Reproducible run manifests
**Description:** Each run writes a manifest with git SHA, config, dataset checksums, model ID and revision, seeds and hardware.
**User story:** As a reviewer, I want to reproduce any reported number.
**Acceptance criteria:** Re-running a manifest with temperature 0 reproduces the metrics, within documented nondeterminism.
**Priority:** Must · **Status:** Planned
