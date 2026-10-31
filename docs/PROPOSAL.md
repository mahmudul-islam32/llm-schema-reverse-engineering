# PhD Research Proposal (Exposé)

## Semantic Data Profiling: Combining Language Models with Dependency Discovery to Reverse-Engineer Undocumented Databases

**Applicant:** Mahmudul Islam · M.Sc. Computer Science, University of Rostock
**Intended group:** Information Systems, Hasso Plattner Institute, University of Potsdam (Prof. Dr. Felix Naumann)
**Artefact:** this repository, an implemented and tested research toolkit (`srev`)

> **Status of this document.** This is a draft exposé, written to start a conversation. The
> numbers in §6 come from the non-LLM benchmark on generated data (three seeds); no LLM
> experiments have been run yet. References marked † should be checked against the original
> sources before formal submission. Check the group's current research focus on its website
> before contacting it, and fill in the bracketed placeholders.

---

### 1. Summary

Many production databases have no declared foreign keys, keys or documentation. Data-profiling
algorithms can find *every* dependency that holds in the data, but most of them hold by accident,
and exact algorithms break on the first dirty value. Humans resolve this with semantics:
`cust_no` probably references `tbl_cust.cust_no`. Large language models (LLMs) encode this kind
of semantics but also hallucinate.

This project asks **when semantic signals from LLMs make data profiling more useful, and when
they mislead it**. It contributes (i) a reproducible benchmark and toolkit that compares
LLM-based, learned, exact and approximate profiling under controlled "legacy" noise, (ii)
methods that combine statistical evidence with semantic plausibility to decide which discovered
dependencies are *genuine*, and (iii) an end-to-end path from an undocumented database to a
verified, normalised relational model, the topic of my master's thesis.

### 2. Background and motivation

**Starting point (master's thesis).** My M.Sc. thesis at the University of Rostock studied
reverse engineering existing databases for the subsequent creation of an explicit relational
model *[thesis title, supervisor, year]*. The central lesson was that the hard part is not
enumerating candidate keys and dependencies but deciding which of them reflect the intended
design.

**Data profiling.** Dependency discovery is a mature field: unique column combinations (UCCs),
functional dependencies (FDs) and inclusion dependencies (INDs) can be discovered efficiently
and completely (e.g. HyFD, HyUCC, BINDER in the Metanome platform), and approximate variants
tolerate dirty data (e.g. Pyro). Two open problems remain for reverse engineering:

1. **Genuineness.** Complete discovery returns orders of magnitude more dependencies than a
   designer intended. In our pilot, the exact profiler returns 45 to 429 minimal FDs per
   database while the curated gold sets have zero to four, giving an FD precision of about 1%.
2. **Semantics.** Names, value formats and query usage carry meaning that statistics ignore.
   Rostin et al. showed that FK discovery benefits from learning over such features; LLMs now
   offer a much richer, but less reliable, source of semantics.

### 3. Research questions

| | Question |
|---|---|
| **RQ1** | How does LLM-only discovery of FKs, UCCs and FDs compare with exact, approximate and learned profiling in precision, recall and F1? |
| **RQ2** | Which evidence (names, statistics, samples, query logs) do LLMs actually use, and which do they need? |
| **RQ3** | How does each method degrade as legacy noise increases (cryptic names, NULLs, typos, orphan references)? |
| **RQ4** | What kinds of hallucinations occur, and how many does data verification remove? |
| **RQ5** | Do hybrids, where profilers enumerate and models rank, beat both components? |
| **RQ6** | Can semantic plausibility separate genuine from accidental FDs, the weakest point of all current methods? |
| **RQ7** | How much do methods rely on names, and on memorised public schemas, rather than on data? |

### 4. Approach

The toolkit implements the full experimental pipeline, so the first year can focus on
experiments rather than infrastructure.

| Component | What it provides | Research question |
|---|---|---|
| Benchmark preparation | Gold labels from declared and curated constraints; seeded noise L0–L3; Spider/BIRD loader for breadth | RQ1, RQ3 |
| Name-reliance probes | Same data with abbreviated, cryptic or *misleading* (permuted) names | RQ7 |
| Exact and approximate profiling | Minimal UCCs, FDs (g3 error), partial INDs, FK candidates; Metanome wrapper | RQ1, RQ3 |
| Learned FK classifier | Rostin-style features (coverage, uniqueness, names, query log), logistic regression, leave-one-dataset-out | RQ1, RQ5 |
| LLM proposer | Evidence-channel ablation, prompt paraphrases, JSON-schema output, cached and replayable | RQ1, RQ2 |
| Hybrid ranking | Profiler candidates scored by an LLM or by rules | RQ5, RQ6 |
| SQL verifier | FK coverage, UCC uniqueness, FD g3 for every proposal | RQ4 |
| Evaluation | P/R/F1 per type, top-k, bootstrap CIs, paired tests, hallucination taxonomy, Cohen's κ | all |
| Synthesis | BCNF decomposition to DDL and an ER diagram with an evidence trace | thesis continuity |

Design principles: open-weight models served with vLLM for reproducibility, temperature 0 with
recorded seeds, verification kept separate from proposal, and run manifests that let every
reported number be re-computed (`srev reproduce --replay`).

### 5. Planned contributions

1. **A benchmark protocol for semantic data profiling** with controlled noise, name probes and a
   hallucination taxonomy, released as open source with reproducible runs.
2. **An empirical study (RQ1–RQ5)** of LLMs against exact, approximate and learned profilers,
   including where semantics help and where they mislead.
3. **Methods for genuine-dependency ranking (RQ6)** that combine dependency statistics
   (redundancy, error, coverage) with semantic plausibility from small open models, aimed at the
   FD precision problem.
4. **LLM-guided search (later stage):** using semantic priors to order or prune the candidate
   lattice of dependency discovery, so profilers evaluate likely-genuine candidates first.
5. **End-to-end reverse engineering:** from an undocumented database to a verified, normalised
   relational model with traceable evidence, extending my master's thesis.

### 6. Preliminary results (benchmark without LLMs)

The full protocol has been run for every method that needs no language model: four generated
databases (TPC-H-shaped, university, legacy retail without declared FKs, airline with a
self-reference), noise levels L0–L3, three name probes, three seeds. Details, precision/recall,
hallucination counts and all caveats: [RESULTS.md](RESULTS.md). Foreign-key macro F1, mean over
three seeds (sd ≤ 0.03 unless shown):

| Method | L0 | L1 | L2 | L3 | misleading names |
|---|---|---|---|---|---|
| Exact profiling (INDs into UCCs) | 0.68 | 0.16 | 0.07 | 0.02 | 0.68 |
| Approximate profiling (partial INDs, g3) | 0.67 | 0.67 | 0.67 | 0.64 | 0.67 |
| Names-only heuristic + verifier | 0.86 | 0.55 ± 0.09 | 0.25 ± 0.09 | – | 0.14 ± 0.20 |
| Names + statistics heuristic + verifier | 0.97 | 0.76 | 0.59 | 0.37 ± 0.06 | 0.71 ± 0.04 |
| Learned FK classifier (no query-log feature) | 0.87 | 0.86 | 0.79 | 0.76 | 0.85 |

What the benchmark shows, and what it leaves for the LLM experiments:

- **Noise breaks exact discovery** (0.68 → 0.16 at the lowest level) but not approximate
  discovery, so LLMs must be compared against an approximate baseline (RQ3).
- **Precision, not completeness, is the bottleneck**: profilers reach FK recall 1.00 at precision
  0.42–0.45, and 88% of the exact profiler's output holds in the data without being intended.
- **Genuine FDs are unsolved** (RQ6): the exact profiler's FD precision is about 1.4% on clean
  data, the best method reaches FD F1 0.53, and semantic FDs such as `zip_code → city` are found
  by none.
- **Names mislead** (RQ7): on identical data with permuted names, the names-only method falls from
  0.86 to 0.14, while data-driven methods are unaffected. This is the decisive test for LLMs.
- **Learned features**: the distinct-value ratio and row coverage dominate; name similarity adds
  little. With a query-log feature the model reaches 0.98 at every level, an artefact of generated
  workloads that mention every gold FK, which motivates real workloads (Spider/BIRD, JOB).

Caveats: four small generated databases with author-designed gold labels, Metanome not yet run,
and four databases are too few for significance tests (minimum p = 0.125). The LLM runs use the
same configuration (`configs/experiment.yaml`) and are the first step of the work plan.

Reproduce: `srev run --config configs/benchmark.yaml --seed 1` (about 1 minute on 4 cores),
repeat for seeds 2 and 3, then `srev summarize` and `srev export latex`.

### 7. Work plan (three years)

| Period | Work | Output |
|---|---|---|
| Months 1–6 | Real benchmarks (TPC-H `dbgen`, Spider/BIRD, a real-world schema with declared FKs); two open model families × two sizes; RQ1–RQ3 | Workshop paper (e.g. at a SIGMOD/VLDB workshop) |
| Months 7–12 | Hallucination study with manual audit (two annotators, κ); verification and hybrid ranking (RQ4, RQ5); name probes (RQ7) | Conference submission |
| Year 2 | Genuine-dependency ranking for FDs and FKs combining statistics and semantics; comparison with learned and approximate baselines (RQ6) | Main conference paper |
| Year 2–3 | LLM-guided candidate ordering and pruning for dependency discovery; efficiency study on large tables | Paper |
| Year 3 | End-to-end reverse engineering to normalised models, case study on a legacy system, dissertation | Dissertation |

### 8. Fit with the Information Systems group

The project builds directly on the group's data-profiling line: complete and approximate
dependency discovery (Metanome, HyFD, HyUCC, BINDER, Pyro) and learned FK discovery (Rostin et
al.). It adds a question these algorithms leave open, namely which discovered dependencies are
*meaningful*, and tests whether today's language models can answer it reliably. The toolkit
already wraps Metanome and implements approximate discovery, so results can be compared with the
group's algorithms from the start.

### 9. Risks and mitigation

| Risk | Mitigation |
|---|---|
| LLMs have memorised public schemas (TPC-H) | Name probes (cryptic, misleading), lesser-known schemas, clean vs. renamed results reported separately |
| Gold labels are incomplete | Manual audit of a sample of false positives, `plausible_not_in_gold` category |
| FD gold is subjective | Two annotators, Cohen's κ, exact-FD gold reported alongside curated gold |
| Compute cost of LLM runs | Small open models first, cached and replayable responses, per-run token accounting |
| Prompt sensitivity | Three paraphrases, multiple seeds, variance reported |

### 10. References

- Abedjan, Golab, Naumann. *Profiling relational data: a survey.* VLDB Journal 24(4), 2015.
- Abedjan, Golab, Naumann, Papenbrock. *Data Profiling.* Synthesis Lectures on Data Management, Morgan & Claypool, 2018. †
- Papenbrock, Naumann. *A Hybrid Approach to Functional Dependency Discovery* (HyFD). SIGMOD 2016.
- Papenbrock, Naumann. *A Hybrid Approach for Efficient Unique Column Combination Discovery* (HyUCC). BTW 2017. †
- Papenbrock et al. *Divide & Conquer-based Inclusion Dependency Discovery* (BINDER). PVLDB 8(7), 2015.
- Papenbrock et al. *Data Profiling with Metanome.* PVLDB 8(12), 2015 (demo).
- Kruse, Naumann. *Efficient Discovery of Approximate Dependencies* (Pyro). PVLDB 11(7), 2018. †
- Rostin, Albrecht, Bauckmann, Naumann, Leser. *A Machine Learning Approach to Foreign Key Discovery.* WebDB 2009.
- Zhang, Hadjieleftheriou, Ooi, Procopiuc, Srivastava. *On Multi-Column Foreign Key Discovery.* PVLDB 3(1), 2010.
- Narayan, Chami, Orr, Arora, Ré. *Can Foundation Models Wrangle Your Data?* PVLDB 16(4), 2022.
- Kwon et al. *Efficient Memory Management for Large Language Model Serving with PagedAttention* (vLLM). SOSP 2023.

---

### Appendix A: first-contact email (template)

> **Subject:** PhD application: semantic data profiling with LLMs (reverse engineering of undocumented databases)
>
> Dear Prof. Naumann,
>
> I hold an M.Sc. in Computer Science from the University of Rostock, where my thesis addressed
> reverse engineering existing databases for the subsequent creation of relational models
> *[title]*. I would like to continue this line of work as a PhD student in your group.
>
> My proposed topic asks when semantic signals from language models make dependency discovery
> more useful, and when they mislead it. Building on your group's work on data profiling and on
> learned foreign-key discovery, I have implemented an open-source toolkit that compares LLM
> proposals with exact, approximate (g3, partial INDs) and learned profilers under controlled
> noise, includes a Metanome wrapper, and verifies every proposal against the data. A pilot on
> generated data already shows that approximate profiling stays robust where exact discovery
> breaks, that name-reliant methods collapse when names mislead, and that genuine FD discovery
> remains the open problem.
>
> Exposé and code: https://github.com/mahmudul-islam32/llm-schema-reverse-engineering
> (exposé in `docs/PROPOSAL.md`).
>
> I would be grateful for the chance to discuss whether this direction fits your group's plans.
>
> Kind regards,
> Mahmudul Islam
