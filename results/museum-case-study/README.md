# Museum case study (held-out domain)

`srev discover` on the museum generator, mean over seeds [1, 2, 3]. The discovery model was trained on tpch_mini, university, retail_legacy and airline only.

| condition | FK P | FK R | FK F1 | UCC F1 | FD F1 | FD recall | FKs to review | classes | many-to-many | candidate concepts |
|---|---|---|---|---|---|---|---|---|---|---|
| none | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 5.00 | 1.00 | 1.00 |
| low | 1.00 | 1.00 | 1.00 | 1.00 | – | 0.00 | 0.00 | 5.00 | 1.00 | 0.00 |
| medium | 1.00 | 0.89 | 0.94 | 1.00 | – | 0.00 | 0.33 | 5.67 | 0.33 | 0.00 |
| names-cryptic | 1.00 | 1.00 | 1.00 | 0.78 | 1.00 | 1.00 | 0.00 | 5.00 | 1.00 | 1.00 |
| names-misleading | 1.00 | 0.94 | 0.97 | 0.61 | 1.00 | 1.00 | 0.67 | 5.33 | 0.67 | 1.00 |

FD F1 is undefined ('–') when discovery proposes no FD; under noise the curated FDs exceed the 0.02 g3 threshold that `discover` uses, so FD recall drops to 0.

Ontology, R2RML mapping and HTML report for seed 1: [`none/`](none/) and [`low/`](low/).
