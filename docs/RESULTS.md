# Benchmark results (non-LLM methods)

> **Scope.** These are the first complete runs of the protocol in [RESEARCH.md](RESEARCH.md)
> for every method that runs without a language model. **No LLM has been evaluated yet**: the
> environment used for these runs had no GPU and no model endpoint. The LLM rows will be added
> by running [`configs/experiment.yaml`](../configs/experiment.yaml) on the same databases (see
> [How to add the LLM runs](#how-to-add-the-llm-runs)). All numbers below are reproducible from
> the committed configs and manifests.

## Setup

| | |
|---|---|
| Config | [`configs/benchmark.yaml`](../configs/benchmark.yaml) |
| Databases | `tpch_mini` (8 tables), `university` (8), `retail_legacy` (6, no declared FKs), `airline` (6, self-reference + parallel FKs); generated, ~1,800–4,200 rows each |
| Conditions | noise L0–L3 and three name probes (abbreviated, cryptic, misleading names on unchanged data) |
| Methods | exact profiling, approximate profiling, learned FK classifier (with / without the query-log feature, leave-one-dataset-out), name heuristic, name+statistics heuristic, heuristic hybrid ranking; each heuristic/learned method also with the SQL verifier (`+verify`) |
| Repetitions | 3 seeds (1, 2, 3); a seed changes the generated data, the noise and the samples |
| Metric | macro F1 over the four databases (FD: three, `tpch_mini` has no FD gold), mean ± sample sd over seeds |
| Runtime | about 1 minute per seed on 4 CPU cores |
| Artefacts | [`results/benchmark/`](../results/benchmark): per-seed `report.md`, `aggregates.csv`, `metrics.csv`, `hallucinations.csv`, `learned.csv`, `manifest.json`; cross-seed [`summary/`](../results/benchmark/summary) with `summary.md`, `summary.csv`, `summary.tex` and seed-averaged noise curves |

Reproduce:

```bash
for s in 1 2 3; do srev run --config configs/benchmark.yaml --seed $s --run-dir results/benchmark/seed$s; done
srev summarize results/benchmark/seed1 results/benchmark/seed2 results/benchmark/seed3 -o results/benchmark/summary
```

## Foreign keys

Macro F1, mean ± sd over 3 seeds:

| Method | L0 | L1 | L2 | L3 | abbreviated | cryptic | misleading |
|---|---|---|---|---|---|---|---|
| Exact profiling | 0.68 ± 0.00 | 0.16 ± 0.04 | 0.07 ± 0.02 | 0.02 ± 0.01 | 0.68 | 0.68 | 0.68 |
| Approximate profiling | 0.67 ± 0.00 | 0.67 ± 0.01 | 0.67 ± 0.01 | 0.64 ± 0.03 | 0.67 | 0.67 | 0.67 |
| Names heuristic + verify | 0.86 ± 0.00 | 0.55 ± 0.09 | 0.25 ± 0.09 | – | 0.82 | – | 0.14 ± 0.20 |
| Names+stats heuristic + verify | 0.97 ± 0.00 | 0.76 ± 0.01 | 0.59 ± 0.02 | 0.37 ± 0.06 | 0.97 | 0.97 | 0.71 ± 0.04 |
| Hybrid (heuristic ranks profiler candidates) | 0.84 ± 0.00 | 0.11 ± 0.04 | 0.08 ± 0.08 | 0.00 | 0.85 | 0.18 | 0.21 ± 0.09 |
| Learned FK, no query-log feature | 0.87 ± 0.01 | 0.86 ± 0.02 | 0.79 ± 0.02 | 0.76 ± 0.03 | 0.87 | 0.85 | 0.85 ± 0.03 |
| Learned FK, with query-log feature | 0.98 | 0.98 | 0.98 | 0.98 | 0.98 | 0.98 | 0.98 |

Precision / recall (micro, mean over seeds):

| Method | L0 P / R | L3 P / R | misleading P / R |
|---|---|---|---|
| Exact profiling | 0.45 / 1.00 | 0.03 / 0.02 | 0.45 / 1.00 |
| Approximate profiling | 0.42 / 1.00 | 0.41 / 0.91 | 0.42 / 1.00 |
| Names+stats heuristic + verify | 0.97 / 0.97 | 1.00 / 0.23 | 0.90 / 0.52 |
| Learned FK, no query log | 0.81 / 0.90 | 0.65 / 0.85 | 0.80 / 0.88 |

## Keys (UCC) and functional dependencies (FD)

| Method | UCC L0 | UCC L3 | UCC misleading | FD L0 | FD L1 | FD L2–L3 |
|---|---|---|---|---|---|---|
| Exact profiling | 0.57 | 0.61 | 0.57 | 0.10 | 0.00 | 0.00 |
| Approximate profiling | 0.51 | 0.50 | 0.51 | 0.07 | 0.05 | ≤ 0.01 |
| Names+stats heuristic + verify | 0.89 | 0.79 | 0.55 | 0.53 | 0.67* | – |
| Hybrid (heuristic) | 0.87 | – | 0.36 | 0.53 | – | – |

\* defined for only some seeds/databases: the heuristic proposes FDs only where names give them away.

## Findings

**RQ3: noise breaks exact discovery, not approximate discovery.** Exact profiling falls from
FK F1 0.68 to 0.16 at the lowest noise level, because a single orphan value invalidates an
inclusion dependency. Approximate profiling (partial INDs over rows, g3 FDs) stays at 0.64–0.67
across all levels. Any LLM comparison therefore needs an approximate baseline, otherwise it
measures robustness to dirt rather than semantic understanding.

**Completeness is not the bottleneck; precision is.** Both profilers reach FK recall 1.00 on
clean data at precision 0.42–0.45. Over three seeds the exact profiler emits 2,182 proposals at
L0, of which 1,960 are false positives: 1,916 hold in the data but are not intended
(`plausible_not_in_gold`) and 44 have the wrong arity. Deciding which dependencies are *genuine*
is the open problem (RQ6).

**RQ6: functional dependencies remain unsolved.** On clean data the exact profiler reaches
FD F1 0.10 (about 1% precision); no method exceeds 0.53, and only the names heuristic gets
there, by recovering FDs whose column names reveal them (`cat_cd → cat_desc`). Semantic FDs such
as `zip_code → city` are found by no method. This is where LLM knowledge could matter most.

**RQ7: names are powerful and dangerous.** With cryptic names the names-only heuristic finds
nothing; with *misleading* names it drops from 0.86 to 0.14 FK F1 and from 0.79 to 0.27 UCC F1,
and the hybrid ranker from 0.84 to 0.21. Data-driven methods are unchanged by renaming, as
expected. The probe isolates name reliance, which is the key test for LLMs and for memorised
public schemas such as TPC-H.

**RQ4: verification helps precision but has a cost under noise.** The SQL verifier lifts the
names heuristic from 0.66 to 0.86 FK F1 on clean data (it removes 42 contradicted proposals over
three seeds). With a fixed 0.95 coverage threshold it also rejects genuine FKs once orphans
accumulate: the learned model drops from 0.76 to 0.50 at L3 when verified. Verification
thresholds should adapt to the measured noise.

**RQ5: rule-based ranking of profiler output is brittle.** The heuristic hybrid matches the
profilers on clean data (0.84) but collapses under noise (0.11 at L1), because its candidates come
from *exact* profiling and its scores from names. Replacing both parts (approximate candidates,
LLM or learned scores) is the obvious next experiment.

**Learned FK discovery is the strongest non-LLM method under noise** (0.76–0.87 without query
logs). Its largest standardised weights are the distinct-value ratio (+3.1), row coverage (+2.1)
and whether the referenced column is the table's first column (+1.1); name similarity contributes
+0.5. With the query-log feature it reaches 0.98 everywhere, but only because the generated
workloads mention every gold FK. This is a benchmark artefact, and real workloads (Spider/BIRD
gold SQL, JOB) are needed before query logs can be credited.

**Variance across seeds is small** (sd ≤ 0.03 for most cells, up to 0.20 for the name-based
methods under misleading names). Which accidental dependencies appear is largely fixed by each
schema's structure, not by the random draw. The paired permutation test over four databases
cannot reach p < 0.125, so significance claims need more databases (Spider/BIRD).

## Threats to validity specific to these runs

- Four small generated databases with gold labels designed by the author; results on real
  schemas may differ.
- The learned model is trained on the same *generators* (other databases, leave-one-dataset-out),
  which favours it compared with a truly unseen domain.
- The heuristic encodes naming conventions similar to those in the generators.
- Metanome's HyFD/HyUCC/BINDER were not run (building the third-party code was not possible in
  this environment); the Python profilers compute the same exact dependency sets up to the
  configured arity, but runtime comparisons are not meaningful.

## How to add the LLM runs

```bash
docker compose --profile gpu up -d vllm        # or any OpenAI-compatible endpoint
export SREV_LLM_BASE_URL=http://localhost:8000/v1 SREV_LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
for s in 1 2 3; do srev run --config configs/experiment.yaml --seed $s --run-dir results/llm/seed$s; done
srev summarize results/llm/seed* -o results/llm/summary
```

`configs/experiment.yaml` uses the same databases and probes plus the LLM ablation (4 evidence
combinations × 3 paraphrases), the LLM hybrid and Metanome (when its JARs are present). Responses
are cached, so `srev reproduce --replay` re-scores a finished run without the server.
