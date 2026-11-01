# Application kit: PhD position – Data Integration (HPI, Prof. Felix Naumann)

Everything here is a draft to adapt. Replace the bracketed placeholders, verify the posting on
the official HPI career portal (see [JOB_MATCH.md](JOB_MATCH.md)), and keep claims to what you can
show.

## Checklist

- [ ] Posting still open; note its reference number and the required documents
- [ ] CV (education, thesis, skills: Python, SQL, PostgreSQL, data profiling, ontology/RDF, ML)
- [ ] Transcripts and degree certificate (M.Sc. Computer Science, University of Rostock)
- [ ] Master's thesis abstract or PDF (*[thesis title]*)
- [ ] Research proposal: [PROPOSAL.md](PROPOSAL.md), exported to PDF (2–4 pages; shorten §6–§8 if a page limit applies)
- [ ] Link to this repository; README, RESULTS and the museum case study are the entry points
- [ ] Optional before submitting: one LLM run (`configs/experiment.yaml`) and the Metanome baselines (see JOB_MATCH.md, "Gaps")
- [ ] Names and contacts of referees
- [ ] Submit through the HPI career portal (as the listing requires); a short email to Prof. Naumann can point to the application

## Cover letter (draft)

> Dear Prof. Naumann, dear members of the Information Systems group,
>
> I am applying for one of the PhD positions in Data Integration. The posting names the Metanome
> project's goal of discovering dependencies and extracting ontologies from large datasets, and
> the Metis project's goal of assessing data quality. These are the two questions my work has led
> me to.
>
> In my M.Sc. thesis in Computer Science at the University of Rostock, *[thesis title]*, I
> reverse-engineered existing databases into explicit relational models. The thesis taught me
> that enumerating candidate keys and dependencies is not the hard part; deciding which of them
> reflect the intended design is. Since then I have built an open-source research toolkit,
> **srev**, to study this question systematically:
> https://github.com/mahmudul-islam32/llm-schema-reverse-engineering
>
> The toolkit implements:
> - exact and approximate discovery of UCCs, FDs and inclusion dependencies, with a Metanome
>   wrapper;
> - a learned foreign-key classifier in the spirit of Rostin et al.;
> - a framework for evaluating language-model proposals against profiling, with SQL
>   verification, controlled noise and name probes;
> - export of verified constraints as an OWL ontology with a W3C R2RML mapping.
>
> A benchmark over four databases, seven conditions and three seeds shows that exact discovery
> collapses on dirty data while approximate discovery does not. It also shows that methods
> relying on names fail when names mislead, and that genuine FD discovery remains unsolved (FD
> precision of exact profiling is about 1.4%). In a held-out museum case study, including a
> self-referencing provenance chain, the pipeline recovers all foreign keys and keys on clean data
> and produces an ontology with a many-to-many relation and a candidate *City* concept found from
> a functional dependency.
>
> For my PhD I propose *semantic data profiling*: ranking discovered dependencies by genuineness
> using statistics, learned models and language models, extracting ontologies from genuine
> dependencies, and using their violations as data-quality signals, with cultural-heritage
> collections as a central application. The attached proposal describes research questions, work
> packages and a three-year plan.
>
> *[One or two sentences on further relevant experience: courses, work, publications, ontology
> engineering, languages (English level; German level if any).]*
>
> I would be glad to discuss how this fits the group's plans.
>
> Kind regards,
> Mahmudul Islam

## Short email to accompany the portal application (draft)

> **Subject:** Application for the PhD position in Data Integration: dependency discovery and ontology extraction
>
> Dear Prof. Naumann,
>
> I have applied through the HPI career portal for one of the PhD positions in Data Integration.
> My background is an M.Sc. in Computer Science from the University of Rostock with a thesis on
> reverse engineering databases into relational models. I have since built an open-source toolkit
> that compares language-model proposals with exact, approximate and learned dependency discovery
> and exports verified constraints as an OWL ontology with an R2RML mapping:
> https://github.com/mahmudul-islam32/llm-schema-reverse-engineering
> (proposal in `docs/PROPOSAL.md`, results in `docs/RESULTS.md`).
>
> I would welcome the opportunity to discuss whether this direction fits the Metanome and Metis
> projects.
>
> Kind regards,
> Mahmudul Islam

## Interview preparation: likely questions and where the answers are

| Question | Where to look |
|---|---|
| How does your FD discovery differ from HyFD? | Python profilers are simple level-wise algorithms for in-memory data; HyFD's sampling + validation hybrid is the efficient baseline the PhD would build on (`srev/baselines/profiling.py` docstring) |
| What is g3 and why use it? | minimum share of rows to delete so the FD holds; used for approximate FDs and verification (`srev/verify.py`) |
| Why do exact profilers have low precision? | accidental dependencies; RESULTS.md "Completeness is not the bottleneck" |
| How do you know LLM proposals are not memorised? | name probes (cryptic, misleading) on identical data; RESULTS.md RQ7 |
| How is the ontology derived? | `srev/ontology.py` module docstring: classes, properties, hasKey, bridges, FD-derived concepts, R2RML |
| What would you do first? | PROPOSAL.md §8: Metanome baselines + LLM study on Spider/BIRD, workshop paper |
