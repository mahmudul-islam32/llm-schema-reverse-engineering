# Matching this project to open positions in Prof. Felix Naumann's group (HPI)

> **Sources and status.** Compiled on 2026-11-01 from web-search results about the HPI
> Information Systems group (the HPI pages themselves could not be opened from the environment
> used, so details come from search-result summaries of the listings). Posting dates and
> deadlines were not visible. **Check every detail on the official listing before applying.**
> Links: [Two PhD positions – Data Integration](https://jobs.plattnerfoundation.org/HPI/job/Potsdam-Two-PhD-positions-(fmx)-Data-Integration-14482/1353574555/) ·
> [PhD or Postdoc researcher (with MIT)](https://jobs.plattnerfoundation.org/HPI/job/Potsdam-PhD-or-Postdoc-researcher-position-(fmx)-14482/1360718255/) ·
> [Group open positions](https://hpi.de/naumann/people/open-positions.html) ·
> [Metis project](https://hpi.de/en/database-group/projects/data-integration-projects/data-quality-and-cleansing/metis/) ·
> [Data profiling and analytics](https://hpi.de/en/naumann/projects/data-profiling-and-analytics.html)

## Positions found

| | Two PhD positions (f/m/x) – Data Integration | PhD or Postdoc researcher (f/m/x), with MIT | PhD/Postdoc scholarships, HPI Research School |
|---|---|---|---|
| Group focus stated | data profiling, data integration, data quality | data quality, integration, cleaning, agentic AI, provenance research | Data Science and Engineering |
| Projects named | **Metis** (data quality assessment, e.g. completeness and correctness; is data fit for AI training) and **Metanome** (profiling algorithms that discover dependencies and *extract ontologies* from large datasets); particular interest in **cultural heritage** | **Mosaic** (joint MIT project): an agentic system that turns research questions into multi-source queries, for provenance research and restitution of Nazi-looted art; research stays at MIT | open topic |
| Requirements | master's in CS or related; experience in at least one of data quality, data integration, data cleaning, ontology engineering; strong English (German optional) | master's or PhD in CS, digital humanities or related; experience in at least one of data quality, integration, cleaning, agentic AI, provenance research | — |
| Terms | full-time, temporary, PhD-qualifying; Potsdam-Babelsberg; open until filled | full-time, three years | deadlines reported as 1 February and 15 August |
| Apply | HPI career portal; contacts Nicola Mertens (HR), Prof. Felix Naumann | HPI career portal; same contacts | per the research-school call |

## Recommendation

**Apply to the "Two PhD positions – Data Integration" first.** Its Metanome track asks for
exactly what this project does (dependency discovery) plus ontology extraction, and its Metis
track asks for data quality assessment. Cultural heritage is named as a domain of interest. Treat
the **MIT/Mosaic position as a second application**: the project's schema discovery, join-path
generation and verification are building blocks for agents that query several undocumented
sources, but agentic query planning itself is not yet covered. Mention the **research-school
scholarship** as a fallback route if the advertised positions are filled.

## Requirement-to-evidence map

| What the posting asks for | Evidence in this repository |
|---|---|
| Profiling algorithms that discover dependencies (Metanome) | exact and approximate UCC/FD/IND discovery (`srev.baselines.profiling`), Metanome wrapper and result parser, learned FK classifier, benchmark over 4 databases × 7 conditions × 3 seeds ([RESULTS.md](RESULTS.md)) |
| Extracting ontologies (Metanome) | `srev export owl`: OWL classes, properties, `owl:hasKey`, many-to-many detection, FD-derived candidate concepts, W3C R2RML mapping, RDF instance data; validated with rdflib |
| Data quality assessment, fitness for use (Metis) | SQL verifier (coverage, uniqueness, g3), `srev check` with drift and schema-change detection, noise injector to measure robustness, dbt tests with warn/error severity |
| Data integration | relational reverse engineering to BCNF models, join-path discovery (`srev graph`), CSV/DataFrame/PostgreSQL/Spider ingestion |
| Data cleaning | approximate dependencies and their violations as cleaning signals; orphan, typo and NULL injection for controlled studies |
| Cultural heritage interest | museum collection generator with a provenance chain; held-out case study from raw tables to ontology ([results/museum-case-study](../results/museum-case-study/README.md)) |
| Research ability | research questions, protocol, hypotheses, threats to validity, reproducible manifests, LaTeX export ([PROPOSAL.md](PROPOSAL.md), [RESEARCH.md](RESEARCH.md)) |
| Master's background | M.Sc. Computer Science, University of Rostock; thesis on reverse engineering existing databases for the subsequent creation of relational models |

## Gaps and how to address them before or in the application

| Gap | Action |
|---|---|
| No LLM results yet | run `configs/experiment.yaml` with one open model (a single GPU suffices) and add the table to RESULTS.md, or state clearly that the LLM study is the first PhD milestone |
| Metanome algorithms not run | build HyFD/HyUCC/BINDER locally and add them as baselines; this directly shows familiarity with the group's tools |
| Ontology extraction evaluated only on generated data | evaluate against a public relational-to-ontology mapping benchmark or a real museum collection export |
| Agentic AI (MIT position only) | optional: a small agent that uses `srev graph` join paths to answer questions over two sources |
| German | optional per the listing; mention level if any |
