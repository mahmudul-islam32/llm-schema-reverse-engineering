# PhD Research Proposal

## Semantic Data Profiling: Discovering Genuine Dependencies and Extracting Ontologies from Undocumented Data

**Applicant:** Mahmudul Islam · M.Sc. Computer Science, University of Rostock
**Intended position:** PhD position – Data Integration, Information Systems group, Hasso Plattner Institute (Prof. Dr. Felix Naumann); see [JOB_MATCH.md](JOB_MATCH.md)
**Artefact:** this repository, an implemented and tested research toolkit (`srev`, 110 tests)

> **Status.** Draft for an application. The numbers in §6 come from the non-LLM benchmark and a
> held-out case study on generated data; no LLM experiment has been run yet. Entries marked † in
> §12 must be checked against the original sources. Fill in the bracketed placeholders.

---

### 1. Summary

Most data that organisations, archives and museums hold is structured but undocumented: keys,
foreign keys and the concepts behind the tables were never declared, or were lost in exports.
Data-profiling algorithms can discover every dependency that holds in such data, but they cannot
tell which of them the designer *meant*, and exact algorithms fail on the first dirty value.
Without that distinction neither a relational model nor an ontology can be reconstructed
reliably, and data quality cannot be judged against the intended structure.

This project develops **semantic data profiling**: dependency discovery that combines statistical
evidence from the data with semantic evidence from names, values, query usage and language models,
and that turns the resulting *genuine* dependencies into a verified relational model and an OWL
ontology with a standard (R2RML) mapping. It contributes:
1. methods to rank discovered dependencies by genuineness;
2. dependency-driven ontology extraction that recovers hidden concepts from functional
   dependencies;
3. the use of dependency violations as data-quality signals;
4. benchmarks and case studies, including cultural-heritage collections.

### 2. Motivation

My M.Sc. thesis at the University of Rostock, *[thesis title]* (supervised by *[name]*,
*[year]*), reverse-engineered existing databases into explicit relational models. Its lesson is
that enumerating candidate keys and dependencies is not the hard part. Deciding which candidates
reflect the intended design is. Three observations from the preliminary work make the problem
concrete:

- **Precision is the bottleneck.** On four generated databases, exact profiling recovers every
  gold foreign key (recall 1.00) at precision 0.45. 88% of its output holds in the data without
  being intended. For FDs, precision is about 1.4%.
- **Dirt breaks exact discovery.** At the lowest noise level, exact FK discovery drops from F1
  0.68 to 0.16, while approximate discovery stays at 0.67.
- **Names help and mislead.** A names-based method falls from F1 0.86 to 0.14 when the same data
  carries permuted names. Data-driven methods are unaffected.

Language models know a lot about how data is usually modelled (`cust_no` probably references a
customer table; `city` determines `country`), but they hallucinate and may have memorised public
schemas. The project asks how to use such semantic knowledge *without* giving up the guarantees of
profiling, and how far it takes us from dependencies to ontologies.

### 3. Background

- **Data profiling** discovers metadata such as unique column combinations (UCCs), functional
  dependencies (FDs) and inclusion dependencies (INDs). Hybrid algorithms (HyFD, HyUCC) and
  divide-and-conquer IND discovery (BINDER) in the Metanome platform make complete discovery
  feasible on large data. Approximate dependency discovery (Pyro) tolerates errors.
- **Foreign-key discovery** builds on INDs. Rostin et al. showed that a learned classifier over
  IND features separates genuine FKs from accidental inclusions. Multi-column FK discovery adds
  combinatorial challenges.
- **Relational-to-RDF mapping** is standardised by the W3C Direct Mapping and R2RML
  recommendations. Both rely on declared keys and foreign keys, which undocumented data lacks.
- **Language models for data management** can match, impute and clean data (e.g. Narayan et al.),
  but their reliability on structure discovery and their tendency to hallucinate are not well
  measured.
- **Data quality assessment** asks whether data is fit for a task, including AI training (e.g.
  the group's Metis project and the *five facets* view of data, source, system, task and human).
  Dependencies are a natural yardstick: violations of genuine dependencies are quality problems,
  and violations of accidental ones are not.

### 4. Research questions

| | Question |
|---|---|
| **RQ1 Genuineness** | Which discovered UCCs, FDs and FKs are intended? How well do statistical, learned and LLM-based signals, and their combinations, separate genuine from accidental dependencies? |
| **RQ2 Robustness** | How should discovery and verification adapt to dirty data so that genuine dependencies survive noise and accidental ones do not? |
| **RQ3 Ontology extraction** | How accurately can an ontology (classes, relations, keys, hidden concepts) be reconstructed from genuine dependencies, compared with reference ontologies and mappings? |
| **RQ4 Name reliance** | How much do methods, and LLMs in particular, rely on names or memorised schemas rather than data, and how can verification contain the resulting hallucinations? |
| **RQ5 Quality** | Can violations of genuine dependencies serve as data-quality indicators, for example for fitness of data for AI training? |

### 5. Approach

**WP1: Genuine-dependency ranking (RQ1, RQ4).** Start from the complete candidate sets of exact
and approximate profilers (Metanome's HyFD, HyUCC and BINDER, and approximate variants). Score
them with:
- statistical features (coverage, redundancy, g3 error, cardinalities);
- schema features (names, types, positions);
- usage features (join frequencies in query logs);
- priors elicited from small open language models.

Compare learned rankers, LLM rankers and hybrids under a fixed budget. Verify every accepted
dependency on the data. Name probes (abbreviated, cryptic and misleading names on identical data)
measure name reliance.

**WP2: Dependency-driven ontology extraction (RQ3).** Extend direct mapping beyond declared
constraints with three mechanisms:
- discovered keys become `owl:hasKey`;
- genuine FKs become object properties, including self-references and many-to-many bridges;
- non-key FDs reveal hidden concepts; for example, `owner.city → country` suggests a *City*
  class, as in a BCNF decomposition.

Generate R2RML mappings so the data can be published as a knowledge graph, with the evidence for
every axiom attached. Evaluate against reference ontologies and mappings, with per-axiom precision
and recall and a modeller study.

**WP3: Dependencies as quality signals, and cultural-heritage case studies (RQ2, RQ5).** Use
violation rates of genuine dependencies (orphans, g3 error, duplicate keys) as data-quality
indicators and monitor them over time. Test whether they predict downstream problems, for example
in training data. Apply the pipeline to collection and provenance data from museums and archives,
where documentation is scarce and relationships such as ownership chains matter.

### 6. Preliminary work

The toolkit `srev` implements the pipeline end to end; source, tests, benchmark configuration and
results are in this repository.
- **Discovery:** exact and approximate profiling, a Metanome wrapper, a learned FK classifier
  (Rostin-style features, leave-one-dataset-out), a prompt builder and client for open LLMs served
  by vLLM, and an SQL verifier.
- **Evaluation:** an evaluation harness with bootstrap CIs and a hallucination taxonomy, a noise
  injector with name probes, and a Spider/BIRD loader.
- **Outputs:** BCNF/DDL synthesis, ontology and R2RML export, dbt tests, data-quality checks with
  drift detection, and an HTML profiling report.

**Benchmark (non-LLM methods, 4 databases × 7 conditions × 3 seeds;** [RESULTS.md](RESULTS.md)**).**
FK macro F1:

| Method | L0 | L1 | L2 | L3 | misleading names |
|---|---|---|---|---|---|
| Exact profiling | 0.68 | 0.16 | 0.07 | 0.02 | 0.68 |
| Approximate profiling | 0.67 | 0.67 | 0.67 | 0.64 | 0.67 |
| Names-only heuristic + verifier | 0.86 | 0.55 | 0.25 | – | 0.14 |
| Learned FK classifier (no query log) | 0.87 | 0.86 | 0.79 | 0.76 | 0.85 |

For FDs no method exceeds F1 0.53, and the exact profiler's FD precision is about 1.4%: genuine
FD discovery is the open problem RQ1 targets.

**Held-out case study: museum collection to ontology** ([results/museum-case-study](../results/museum-case-study/README.md)).
A museum schema (artists, objects, owners, a self-referencing provenance chain, exhibitions and
loans) that the discovery model never saw in training. One-shot discovery followed by ontology
extraction, mean over three seeds:

| Condition | FK P / R | UCC F1 | FD recall | Ontology |
|---|---|---|---|---|
| clean | 1.00 / 1.00 | 1.00 | 1.00 | 5 classes, loan as many-to-many property, *City* as candidate concept |
| noise L1 | 1.00 / 1.00 | 1.00 | 0.00 | 5 classes, many-to-many kept; FDs lost to noise |
| noise L2 | 1.00 / 0.89 | 1.00 | 0.00 | 5.7 classes on average (missed FKs break the bridge) |
| cryptic names | 1.00 / 1.00 | 0.78 | 1.00 | structure kept; names meaningless |
| misleading names | 1.00 / 0.94 | 0.61 | 1.00 | structure mostly kept |

The case study shows the full path the project targets (undocumented tables → verified
dependencies → ontology and mapping). It also shows the next problems: FDs disappear under noise
(RQ2), and key choice degrades without meaningful names (RQ4).

### 7. Evaluation methodology

- **Data:**
  - generated databases with controlled noise;
  - public benchmarks with annotated keys (TPC-H, Spider/BIRD, IMDB/JOB join graph);
  - real collection data (museum and archive exports) with expert-curated gold sets;
  - reference ontologies or mappings where available.
- **Metrics:**
  - precision, recall and F1 per dependency type, exact and overlap match, top-k at a fixed
    budget;
  - per-axiom precision and recall for ontologies;
  - hallucination categories;
  - cost (runtime, tokens).
- **Statistics:** several seeds, bootstrap CIs and paired tests over databases. Breadth from
  Spider/BIRD makes the tests meaningful; with four databases the minimum attainable p is 0.125.
- **Reproducibility:** pinned open models, temperature 0, cached responses and run manifests,
  so every number can be re-computed.

### 8. Work plan

| Period | Work | Planned output |
|---|---|---|
| Months 1–6 | Metanome baselines; LLM study on generated and Spider/BIRD databases (RQ1, RQ4); first real collection dataset | workshop paper on LLMs vs. profiling for dependency discovery |
| Months 7–15 | Genuine-dependency ranking combining profiler candidates, learned and LLM signals (WP1); robust verification (RQ2) | conference paper |
| Months 16–26 | Dependency-driven ontology extraction and evaluation against reference ontologies (WP2) | conference paper and open dataset |
| Months 24–32 | Dependencies as quality signals; cultural-heritage case studies (WP3) | journal or conference paper |
| Months 30–36 | Integration, dissertation | dissertation, released toolkit |

### 9. Expected contributions

1. Methods that separate genuine from accidental dependencies by combining profiling with semantic
   signals, verified on data.
2. Dependency-driven ontology extraction for undocumented relational data, including discovery of
   hidden concepts, with standard R2RML output.
3. Evidence on when language models help or mislead structure discovery (name reliance,
   hallucinations, memorisation).
4. Dependency-based data-quality indicators.
5. Open benchmarks, case studies and a reproducible toolkit.

### 10. Fit with the group

The project continues the group's line from complete and approximate dependency discovery
(Metanome, HyFD, HyUCC, BINDER, Pyro) and learned FK discovery to the two topics named in the
current posting: **extracting ontologies with profiling algorithms** and **assessing data quality,
including fitness for AI training**. It brings an existing toolkit that already wraps Metanome,
implements approximate discovery and ontology export, and includes a cultural-heritage case study.
It also connects to the provenance-research theme of the group's MIT collaboration, where
undocumented sources must be understood before they can be queried together.

### 11. Risks and mitigation

| Risk | Mitigation |
|---|---|
| LLMs have memorised public schemas | name probes, lesser-known and real collection data, clean vs. renamed results reported separately |
| Gold labels incomplete or subjective (especially FDs, ontologies) | expert curation, two annotators with Cohen's κ, manual audit of `plausible_not_in_gold` |
| Ontology references are scarce | build gold ontologies with domain experts for a few collections; per-axiom evaluation |
| Compute cost | small open models first, caching, token accounting |
| Real data access (cultural heritage) | public open-access collection exports; synthetic generators for controlled studies |

### 12. References

- Abedjan, Golab, Naumann. *Profiling relational data: a survey.* VLDB Journal 24(4), 2015.
- Abedjan, Golab, Naumann, Papenbrock. *Data Profiling.* Synthesis Lectures on Data Management, Morgan & Claypool, 2018. †
- Papenbrock, Naumann. *A Hybrid Approach to Functional Dependency Discovery* (HyFD). SIGMOD 2016.
- Papenbrock, Naumann. *A Hybrid Approach for Efficient Unique Column Combination Discovery* (HyUCC). BTW 2017. †
- Papenbrock et al. *Divide & Conquer-based Inclusion Dependency Discovery* (BINDER). PVLDB 8(7), 2015.
- Papenbrock et al. *Data Profiling with Metanome.* PVLDB 8(12), 2015 (demo).
- Kruse, Naumann. *Efficient Discovery of Approximate Dependencies* (Pyro). PVLDB 11(7), 2018. †
- Rostin, Albrecht, Bauckmann, Naumann, Leser. *A Machine Learning Approach to Foreign Key Discovery.* WebDB 2009.
- Zhang, Hadjieleftheriou, Ooi, Procopiuc, Srivastava. *On Multi-Column Foreign Key Discovery.* PVLDB 3(1), 2010.
- Arenas, Bertails, Prud'hommeaux, Sequeda (eds.). *A Direct Mapping of Relational Data to RDF.* W3C Recommendation, 2012.
- Das, Sundara, Cyganiak (eds.). *R2RML: RDB to RDF Mapping Language.* W3C Recommendation, 2012.
- *The Five Facets of Data Quality Assessment.* ACM SIGMOD Record, 2025 (HPI Information Systems group). †
- Narayan, Chami, Orr, Arora, Ré. *Can Foundation Models Wrangle Your Data?* PVLDB 16(4), 2022.
- Kwon et al. *Efficient Memory Management for Large Language Model Serving with PagedAttention* (vLLM). SOSP 2023.
