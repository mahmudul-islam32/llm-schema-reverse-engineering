# Research Plan

> Status: **draft**. The protocol below is implemented in the `srev` package (see the README), but no experiments with real models have been run. Every result listed below is planned.

## 1. Background and thesis link
The author's M.Sc. thesis studied reverse engineering existing databases into explicit relational models. This project extends that work with LLMs as a semantic signal and asks a sharper, measurable question about when that signal helps.

## 2. Research questions
- **RQ1:** On benchmark databases with constraints removed, how does LLM-only discovery of FKs, UCCs and FDs compare with statistical profiling in precision, recall and F1?
- **RQ2:** Which evidence channels (names, samples, statistics, query logs) contribute most? (ablation)
- **RQ3:** How does performance degrade as noise increases (cryptic names, nulls, orphans), and does the LLM degrade differently from data-driven methods?
- **RQ4:** What kinds of hallucinations occur, and how many does data verification (hybrid mode) remove?
- **RQ5 (exploratory):** Does a hybrid of baseline candidates plus LLM ranking beat both components?

**Hypotheses:** H1: LLMs reach higher FK *precision* than raw IND-based candidates on clean names, but lower recall. H2: The advantage shrinks as name noise increases. H3: Verification removes most hallucinations of the "contradicted by data" type.

## 3. Related work (verified pointers)
- Abedjan, Golab, Naumann. *Profiling relational data: a survey.* VLDB Journal 24(4), 2015.
- Papenbrock et al. *Functional Dependency Discovery: An Experimental Evaluation of Seven Algorithms.* PVLDB 8(10), 2015.
- Papenbrock, Naumann. *A Hybrid Approach to Functional Dependency Discovery* (HyFD). SIGMOD 2016.
- Papenbrock et al. *Data Profiling with Metanome.* PVLDB 8(12), 2015 (demo).
- Papenbrock et al. *Divide & Conquer-based Inclusion Dependency Discovery* (BINDER). PVLDB 8(7), 2015.
- Rostin, Albrecht, Bauckmann, Naumann, Leser. *A Machine Learning Approach to Foreign Key Discovery.* WebDB 2009.
- Zhang, Hadjieleftheriou, Ooi, Procopiuc, Srivastava. *On Multi-Column Foreign Key Discovery.* PVLDB 3(1), 2010.
- Narayan, Chami, Orr, Arora, Ré. *Can Foundation Models Wrangle Your Data?* PVLDB 16(4), 2022.
- Kwon et al. *Efficient Memory Management for Large Language Model Serving with PagedAttention* (vLLM). SOSP 2023.
- Leis et al. *How Good Are Query Optimizers, Really?* PVLDB 9(3), 2015 (Join Order Benchmark on IMDB, a source of realistic join workloads).

*To do:* add a systematic search (2023 onward) for LLM-based schema matching and constraint discovery, and verify each entry before citing it.

## 4. Datasets (candidates, licences to confirm before use)
| Dataset | Why | Gold constraints | Query workload |
|---|---|---|---|
| TPC-H (dbgen) | Standard, clean, well-known names (risk of memorisation) | Declared PK/FK in spec | 22 TPC-H queries |
| IMDB (as used by the Join Order Benchmark) | Real data with no declared FKs. Join keys from the JOB queries | Curated from the JOB join graph | 113 JOB queries |
| MusicBrainz database dump | Large real-world PostgreSQL schema with declared FKs | Declared | — |
| Spider / BIRD text-to-SQL benchmarks | Hundreds of small DBs across domains with schema metadata, including FKs | Provided FK annotations | Gold SQL queries |
| Metanome example datasets (HPI) | Used in profiling papers. Enables FD comparison | FD results from the literature or exact algorithms | — |

FD ground truth: computed with an exact algorithm (e.g. HyFD) on the *clean* data, restricted to minimal, non-trivial FDs. Then a curated "semantic FD" subset is labelled, because many exact FDs are accidental.

## 5. Evaluation protocol
1. Load each DB, export gold, strip constraints, and apply noise level L ∈ {0, 1, 2, 3}.
2. Run the methods: (a) Metanome baselines, (b) LLM-only per evidence combination, (c) LLM + verifier, (d) hybrid ranking (RQ5).
3. At least 2 model families × 2 sizes. Temperature 0. 3 seeds or prompt paraphrases to estimate variance.
4. Compare with gold at exact match. Report column-set overlap as a secondary measure.
5. Use 1,000-sample bootstrap confidence intervals and paired tests across databases.

## 6. Metrics
- For each type T ∈ {FK, UCC, FD}: **Precision** = TP/(TP+FP), **Recall** = TP/(TP+FN), **F1** = 2PR/(P+R). Micro and macro averages across DBs.
- FK direction matters: `A→B` and `B→A` are different.
- **Hallucination rate** = proposals referencing non-existent columns or contradicted by data ÷ all proposals.
- **Parse-failure rate** of the structured output.
- **Cost:** wall-clock time, GPU-hours and tokens for LLMs; runtime and memory for baselines.
- Baselines are scored on the *full* candidate set and also after a top-k cut, because raw IND/FD output is usually far larger than the gold set.

## 7. Threats to validity
- **Memorisation:** public schemas such as TPC-H may be in pretraining data. Mitigate with cryptic renaming (noise) and lesser-known DBs, and report clean vs renamed results separately.
- **Gold incompleteness:** declared constraints can miss true relationships, so some "false positives" may be correct. A manual audit of a sample of FPs is planned.
- **FD gold ambiguity:** the line between accidental and semantic FDs is subjective. Use two annotators and report Cohen's κ.
- **Prompt sensitivity:** results may depend on wording. Use multiple paraphrases.
- **Model and version drift:** pin checkpoints and revisions in manifests.
- **Sample size:** a few DBs limit generality. Spider/BIRD provide breadth.
- **Fairness of baselines:** Metanome algorithms find *all* valid dependencies, not *intended* ones. Use the same post-filtering budget for all methods.

## 8. Research roadmap
| Phase | Output |
|---|---|
| Month 0 | Finalised protocol, dataset licences, literature table |
| Month 1 | Data infrastructure, gold sets, noise injector, pilot on TPC-H |
| Month 2 | Baselines, LLM pipeline, first ablation (RQ1, RQ2) |
| Month 3 | Noise curves and hallucination study (RQ3, RQ4), hybrid (RQ5) |
| Month 4 | Paper draft (workshop or preprint), public artefact release |

Possible venues to target, depending on results: database and data-management workshops (e.g. at SIGMOD or VLDB) or the BTW conference in Germany.

## 9. Fit with PhD applications
Relevant to data-profiling, data-management and data-engineering-for-AI groups. Before contacting a group, check its current research focus on its own website.
