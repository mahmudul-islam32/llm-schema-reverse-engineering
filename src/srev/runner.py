"""Experiment runner: datasets × noise levels × methods → stored results.

For every database copy the runner (1) builds gold labels and the noisy
working copy, (2) extracts evidence and mines the workload, (3) runs every
method variant, (4) verifies every proposal with SQL, (5) evaluates the raw
and the verified proposal sets, and (6) writes artefacts, a manifest and a
Markdown report to ``runs/<timestamp>_<name>/``.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from srev import report as report_mod
from srev.baselines import metanome
from srev.baselines.profiling import ProfilingConfig, discover_fds, load_tables, profile
from srev.baselines.profiling import _Partitions as Partitions
from srev.config import DatasetSpec, ExperimentConfig, MethodSpec
from srev.datasets import load_dataset
from srev.datasets.base import Dataset
from srev.datasets.spider import load_spider
from srev.db import Connector, PostgresConnector, SQLiteConnector, connect, dataset_from_connector
from srev.db import materialize as materialize_db
from srev.eval.hallucination import analyse
from srev.eval.metrics import TypeScore, aggregate, paired_permutation_test, score
from srev.extract import Evidence, extract
from srev.learn import Candidate, candidates, label, train_and_predict
from srev.llm.client import ChatClient, LLMConfig
from srev.llm.prompt import Prompt
from srev.llm.proposers import HeuristicProposer, HybridRanker, LLMProposer, ProposerOutput
from srev.manifest import DatasetRecord, Manifest
from srev.model import CONSTRAINT_TYPES, ConstraintKey, Proposal, Verdict, dedupe, qualify
from srev.noise import NoiseConfig, inject, resolve_profile
from srev.qlog import JoinStats, load_workload, mine_joins
from srev.synth import synthesize
from srev.truth import GoldSet, gold_from_dataset
from srev.verify import Verifier

log = logging.getLogger("srev")


@dataclass
class DbContext:
    db_id: str
    dataset: str
    noise: str
    conn: Connector
    evidence: Evidence
    joins: JoinStats
    gold: GoldSet
    verifier: Verifier
    source_schema: str
    verdicts: dict[ConstraintKey, Verdict] = field(default_factory=dict)
    profile_cache: dict[str, ProposerOutput] = field(default_factory=dict)


@dataclass
class VariantRun:
    variant: str
    method: MethodSpec
    output: ProposerOutput
    channels: list[str] = field(default_factory=list)


def noise_label(level: str | int | NoiseConfig, index: int) -> str:
    if isinstance(level, NoiseConfig):
        return f"custom{index}"
    text = str(level).lower()
    return f"L{text}" if text.isdigit() else text


def _fmt_channels(channels: list[str]) -> str:
    return "+".join(channels)


class Runner:
    def __init__(self, config: ExperimentConfig, run_dir: Path | None = None) -> None:
        self.cfg = config
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = f"{stamp}_{config.name}"
        self.run_dir = run_dir or Path(config.output_dir) / self.run_id
        self.metrics: list[dict[str, Any]] = []
        self.costs: list[dict[str, Any]] = []
        self.halluc: list[dict[str, Any]] = []
        self.synth_index: list[dict[str, Any]] = []
        self._clients: dict[str, ChatClient] = {}
        # learned methods are trained after all databases are prepared
        self._learned_pool: dict[str, list[tuple[DbContext, list[Candidate]]]] = defaultdict(list)
        self.learned_rows: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def run(self) -> Path:
        rd = self.run_dir
        for sub in ("gold", "noise", "evidence", "prompts", "synth", "db"):
            (rd / sub).mkdir(parents=True, exist_ok=True)
        resolved = self.cfg.model_dump(mode="json", by_alias=True)
        (rd / "config.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False))
        manifest = Manifest.start(self.run_id, resolved, self.cfg.seed)
        manifest.llm = self._llm_manifest()
        self._proposals_fh = (rd / "proposals.jsonl").open("w")
        self._fp_fh = (rd / "false_positives.jsonl").open("w")
        try:
            specs = [s for spec in self.cfg.datasets for s in spec.expand()]
            for spec in specs:
                clean, gold = self._load(spec)
                for i, level in enumerate(self.cfg.noise):
                    label = noise_label(level, i)
                    record = self._run_db(spec, clean, gold, level, label, manifest)
                    manifest.datasets.append(record)
            self._run_learned(manifest)
        finally:
            self._proposals_fh.close()
            self._fp_fh.close()
            for client in self._clients.values():
                client.close()
        self._write_tables()
        manifest.finish()
        manifest.save(rd / "manifest.json")
        report_mod.write_report(rd)
        log.info("run finished: %s", rd)
        return rd

    # ------------------------------------------------------------------
    def _llm_manifest(self) -> dict[str, Any]:
        llm_methods = [
            m
            for m in self.cfg.methods
            if m.kind == "llm" or (m.kind == "hybrid" and m.scorer == "llm")
        ]
        if not llm_methods:
            return {}
        out = {}
        for m in llm_methods:
            c = m.llm or self.cfg.llm
            out[m.name] = {
                "base_url": c.base_url,
                "model": c.model,
                "revision": c.revision,
                "temperature": c.temperature,
                "top_p": c.top_p,
                "max_tokens": c.max_tokens,
                "seeds": m.seeds or [c.seed],  # type: ignore[list-item]
                "response_format": c.response_format,
            }
        return out

    def _load(self, spec: DatasetSpec) -> tuple[Dataset, GoldSet]:
        if spec.source == "builtin":
            ds = load_dataset(spec.generator or spec.name, seed=self.cfg.seed, **spec.params)
            ds.name = spec.name
        elif spec.source == "spider":
            ds = load_spider(
                spec.path or "",
                spec.db_id or "",
                tables_json=spec.tables_json,
                db_dir=spec.db_dir,
                queries_json=spec.queries_json,
                max_rows=spec.max_rows,
            )
            ds.name = spec.name
        else:
            conn = (
                SQLiteConnector(spec.path or "")
                if spec.source == "sqlite"
                else connect(dsn=spec.dsn, schema=spec.db_schema)
            )
            with conn:
                ds = dataset_from_connector(conn, spec.name, max_rows=spec.max_rows)
        if spec.workload:
            ds.queries = [w.query for w in load_workload(spec.workload)]
        gold = gold_from_dataset(ds)
        if spec.curated_gold:
            extra = GoldSet.load(spec.curated_gold)
            known = {g.key() for g in gold.labels}
            gold.labels += [
                g.model_copy(update={"source": "curated"})
                for g in extra.labels
                if g.key() not in known
            ]
        if self.cfg.eval.fd_gold in ("exact", "both"):
            exact = self._exact_fds(ds)
            if self.cfg.eval.fd_gold == "exact":
                gold.labels = [g for g in gold.labels if g.type != "FD"]
            known = {g.key() for g in gold.labels}
            gold.labels += [f for f in exact if f.key() not in known]
        return ds, gold

    def _exact_fds(self, ds: Dataset) -> list[Proposal]:
        with SQLiteConnector(":memory:") as conn:
            materialize_db(ds, conn)
            out = []
            for t in load_tables(conn):
                parts = Partitions(t)
                for lhs, rhs, _ in discover_fds(t, self.cfg.eval.exact_fd_max_lhs, parts, []):
                    out.append(
                        Proposal(
                            type="FD",
                            lhs=[qualify(t.name, c) for c in lhs],
                            rhs=[qualify(t.name, rhs)],
                            source="exact",
                            rationale="exact minimal FD on clean data",
                        )
                    )
        return out

    def _working_connector(self, db_id: str) -> tuple[Connector, str]:
        db = self.cfg.database
        if db.backend == "sqlite":
            path = self.run_dir / "db" / f"{db_id}.sqlite"
            path.unlink(missing_ok=True)
            return SQLiteConnector(path), "main"
        schema = f"{db.schema_prefix}_{db_id}".lower()[:63]
        if not db.dsn:
            raise ValueError("database.dsn is required for the postgres backend")
        return PostgresConnector(db.dsn, schema=schema), schema

    # ------------------------------------------------------------------
    def _run_db(
        self,
        spec: DatasetSpec,
        clean: Dataset,
        gold: GoldSet,
        level: str | int | NoiseConfig,
        label: str,
        manifest: Manifest,
    ) -> DatasetRecord:
        rd = self.run_dir
        db_id = f"{spec.name}_{label}"
        log.info("[%s] preparing (noise=%s)", db_id, label)
        noisy, noise_log = inject(clean, resolve_profile(level), seed=self.cfg.seed, gold=gold)
        gold_w = noise_log.renames.gold(gold)
        gold_w.database = db_id
        gold_w.save(rd / "gold" / f"{db_id}.json")
        (rd / "noise" / f"{db_id}.json").write_text(noise_log.model_dump_json(indent=2))

        conn, source_schema = self._working_connector(db_id)
        with conn:
            materialize_db(noisy, conn)
            evidence = extract(
                conn,
                db_id,
                rows_per_table=self.cfg.sample.rows_per_table,
                top_k=self.cfg.sample.top_k,
                seed=self.cfg.seed,
            )
            evidence.save(rd / "evidence" / f"{db_id}.json")
            joins = mine_joins(noisy.queries, noisy.schema_map())
            joins.save(rd / "evidence" / f"{db_id}.joins.json")
            ctx = DbContext(
                db_id=db_id,
                dataset=spec.name,
                noise=label,
                conn=conn,
                evidence=evidence,
                joins=joins,
                gold=gold_w,
                verifier=Verifier(conn, self.cfg.verify),
                source_schema=source_schema,
            )
            results: list[tuple[VariantRun, list[Proposal], float]] = []
            for method in self.cfg.methods:
                if method.kind == "learned":
                    self._collect_candidates(method, ctx)
                    continue
                for vr in self._run_method(method, ctx):
                    start = time.perf_counter()
                    props = dedupe(vr.output.proposals)
                    self._verify(ctx, props)
                    verify_s = time.perf_counter() - start
                    self._save_prompts(vr.output.prompts)
                    self._record(ctx, vr, props, verified=False, verify_seconds=0.0)
                    results.append((vr, props, verify_s))
                    if method.verify:
                        accepted = [p for p in props if ctx.verdicts[p.key()].accepted]
                        verified = VariantRun(
                            f"{vr.variant}+verify", vr.method, vr.output, vr.channels
                        )
                        self._record(
                            ctx, verified, accepted, verified=True, verify_seconds=verify_s
                        )
                        results.append((verified, accepted, verify_s))
            if self.cfg.synth.enabled:
                self._synthesize(ctx, results)
        return DatasetRecord(
            db_id=db_id,
            dataset=spec.name,
            noise=label,
            clean_checksum=clean.checksum(),
            noisy_checksum=noisy.checksum(),
            rows=noisy.n_rows,
            tables=len(noisy.tables),
            gold_counts=gold_w.counts(),
            noise_summary=noise_log.summary(),
        )

    # ------------------------------------------------------------------
    def _client(self, cfg: LLMConfig) -> ChatClient:
        if cfg.cache_dir is None:
            cfg = cfg.model_copy(update={"cache_dir": str(self.run_dir / "llm_cache")})
        key = cfg.model_dump_json()
        if key not in self._clients:
            self._clients[key] = ChatClient(cfg)
        return self._clients[key]

    def _profile(self, ctx: DbContext, cfg: ProfilingConfig) -> ProposerOutput:
        key = cfg.model_dump_json()
        if key not in ctx.profile_cache:
            res = profile(ctx.conn, cfg)
            ctx.profile_cache[key] = ProposerOutput(proposals=res.proposals, seconds=res.seconds)
        return ctx.profile_cache[key]

    def _run_method(self, m: MethodSpec, ctx: DbContext) -> list[VariantRun]:
        log.info("[%s] method %s (%s)", ctx.db_id, m.name, m.kind)
        if m.kind == "baseline":
            return [VariantRun(m.name, m, self._profile(ctx, m.profiling))]
        if m.kind == "metanome":
            start = time.perf_counter()
            try:
                props, runs = metanome.run(
                    ctx.conn, self.run_dir / "metanome" / ctx.db_id, m.metanome
                )
            except metanome.MetanomeUnavailable as exc:
                log.warning("[%s] skipping %s: %s", ctx.db_id, m.name, exc)
                return []
            out = ProposerOutput(proposals=props, seconds=round(time.perf_counter() - start, 3))
            (self.run_dir / "metanome" / ctx.db_id / "runs.json").write_text(
                json.dumps([r.model_dump() for r in runs], indent=2)
            )
            return [VariantRun(m.name, m, out)]
        if m.kind == "heuristic":
            return [
                VariantRun(
                    f"{m.name}[{_fmt_channels(ch)}]",
                    m,
                    HeuristicProposer(ch).propose(ctx.evidence, ctx.joins),
                    ch,
                )
                for ch in m.evidence
            ]
        if m.kind == "hybrid":
            candidates = self._profile(ctx, m.profiling)
            client = self._client(m.llm or self.cfg.llm) if m.scorer == "llm" else None
            ranker = HybridRanker(
                scorer=m.scorer,
                client=client,
                prompt_config=self.cfg.prompt.model_copy(update={"channels": m.evidence[0]}),
                threshold=m.threshold,
                max_candidates=m.max_candidates,
            )
            out = ranker.rank(ctx.evidence, ctx.joins, candidates.proposals)
            out.seconds = round(out.seconds + candidates.seconds, 3)
            return [VariantRun(m.name, m, out, m.evidence[0])]
        # llm
        base = m.llm or self.cfg.llm
        seeds: list[int | None] = list(m.seeds) if m.seeds else [base.seed]
        variants: list[VariantRun] = []
        for ch in m.evidence:
            for para in m.paraphrases:
                for seed in seeds:
                    client = self._client(base.model_copy(update={"seed": seed}))
                    pc = self.cfg.prompt.model_copy(update={"channels": ch, "paraphrase": para})
                    name = f"{m.name}[{_fmt_channels(ch)}]"
                    if len(m.paraphrases) > 1:
                        name += f"#p{para}"
                    if len(seeds) > 1:
                        name += f"@s{seed}"
                    variants.append(
                        VariantRun(
                            name, m, LLMProposer(client, pc).propose(ctx.evidence, ctx.joins), ch
                        )
                    )
        return variants

    def _collect_candidates(self, m: MethodSpec, ctx: DbContext) -> None:
        """Compute features and verdicts while the working copy is open."""
        log.info("[%s] method %s (learned): collecting candidates", ctx.db_id, m.name)
        start = time.perf_counter()
        cands = label(candidates(ctx.conn, ctx.joins, m.learned), ctx.gold.labels)
        self._verify(ctx, [c.proposal(1.0) for c in cands])
        ctx.profile_cache[f"learned:{m.name}:seconds"] = ProposerOutput(
            seconds=round(time.perf_counter() - start, 3)
        )
        self._learned_pool[m.name].append((ctx, cands))

    def _run_learned(self, manifest: Manifest) -> None:
        """Leave-one-dataset-out: train on every other dataset, score the held-out one."""
        for m in self.cfg.methods:
            if m.kind != "learned":
                continue
            pool = self._learned_pool.get(m.name, [])
            datasets = sorted({ctx.dataset for ctx, _ in pool})
            if len(datasets) < 2:
                msg = f"method {m.name}: leave-one-dataset-out needs at least two datasets"
                log.warning(msg)
                manifest.notes.append(msg)
                continue
            for held_out in datasets:
                train = [c for ctx, cs in pool if ctx.dataset != held_out for c in cs]
                if not any(c.label for c in train):
                    manifest.notes.append(
                        f"method {m.name}: no positive training data without {held_out}"
                    )
                    continue
                model = None
                for ctx, cands in pool:
                    if ctx.dataset != held_out:
                        continue
                    start = time.perf_counter()
                    props, model = train_and_predict(train, cands, m.learned)
                    prep = ctx.profile_cache[f"learned:{m.name}:seconds"].seconds
                    out = ProposerOutput(
                        proposals=props, seconds=round(prep + time.perf_counter() - start, 3)
                    )
                    variant = VariantRun(m.name, m, out)
                    self._record(ctx, variant, dedupe(props), verified=False, verify_seconds=0.0)
                    if m.verify:
                        accepted = [p for p in props if ctx.verdicts[p.key()].accepted]
                        verified = VariantRun(f"{m.name}+verify", m, out)
                        self._record(ctx, verified, accepted, verified=True, verify_seconds=0.0)
                if model is not None:
                    n_pos = sum(c.label for c in train)
                    for feature, weight in model.coefficients().items():
                        self.learned_rows.append(
                            {
                                "method": m.name,
                                "held_out": held_out,
                                "feature": feature,
                                "weight": weight,
                                "train_candidates": len(train),
                                "train_positives": n_pos,
                            }
                        )

    def _verify(self, ctx: DbContext, props: list[Proposal]) -> None:
        for p in props:
            if p.key() not in ctx.verdicts:
                ctx.verdicts[p.key()] = ctx.verifier.verify(p)

    def _save_prompts(self, prompts: list[Prompt]) -> None:
        for p in prompts:
            path = self.run_dir / "prompts" / f"{p.sha256}.txt"
            if not path.exists():
                path.write_text(f"### SYSTEM\n{p.system}\n\n### USER\n{p.user}\n")

    # ------------------------------------------------------------------
    def _record(
        self,
        ctx: DbContext,
        vr: VariantRun,
        props: list[Proposal],
        *,
        verified: bool,
        verify_seconds: float,
    ) -> None:
        cfg = self.cfg
        base = {
            "db_id": ctx.db_id,
            "dataset": ctx.dataset,
            "noise": ctx.noise,
            "variant": vr.variant,
            "method": vr.method.name,
            "kind": vr.method.kind,
            "verified": verified,
            "channels": _fmt_channels(vr.channels),
        }
        gold_keys = {g.key() for g in ctx.gold.labels}
        for p in props:
            v = ctx.verdicts.get(p.key())
            self._proposals_fh.write(
                json.dumps(
                    {
                        **base,
                        "id": p.ident(),
                        "proposal": p.model_dump(),
                        "verdict": v.model_dump() if v else None,
                        "in_gold": p.key() in gold_keys,
                    }
                )
                + "\n"
            )
        card = score(
            props,
            ctx.gold.labels,
            n_boot=cfg.eval.bootstrap,
            seed=cfg.seed,
            top_k_factor=cfg.eval.top_k_factor,
        )
        for ctype in CONSTRAINT_TYPES:
            s = card.scores[ctype]
            k = card.top_k.get(ctype)
            self.metrics.append(
                {
                    **base,
                    **s.model_dump(),
                    "topk_precision": k.precision if k else None,
                    "topk_recall": k.recall if k else None,
                    "topk_f1": k.f1 if k else None,
                }
            )
        hr = analyse(props, ctx.gold.labels, ctx.evidence.has_ref, ctx.verdicts)
        self.halluc.append(
            {
                **base,
                "n_proposals": hr.n_proposals,
                "n_false_positives": hr.n_false_positives,
                "hallucination_rate": hr.hallucination_rate,
                **hr.counts,
            }
        )
        for item in hr.items:
            self._fp_fh.write(
                json.dumps(
                    {
                        **base,
                        "id": item.proposal.ident(),
                        "category": item.category,
                        "proposal": item.proposal.model_dump(),
                        "verdict": item.verdict.model_dump() if item.verdict else None,
                    }
                )
                + "\n"
            )
        out = vr.output
        self.costs.append(
            {
                **base,
                "n_proposals": len(props),
                "seconds": round(out.seconds + verify_seconds, 3),
                "tokens": out.tokens,
                "prompt_tokens_est": sum(p.est_tokens for p in out.prompts),
                "prompts": ";".join(p.sha256[:12] for p in out.prompts),
                "cached_responses": sum(c.cached for c in out.completions),
                "responses": out.parse.responses,
                "response_failures": out.parse.response_failures,
                "items": out.parse.items,
                "item_failures": out.parse.item_failures,
                "fence_stripped": out.parse.fence_stripped,
                "trimmed": ";".join(t for p in out.prompts for t in p.trimmed),
            }
        )

    def _synthesize(
        self, ctx: DbContext, results: list[tuple[VariantRun, list[Proposal], float]]
    ) -> None:
        if not results:
            return
        wanted = self.cfg.synth.variant
        chosen = None
        if wanted:
            chosen = next((r for r in results if r[0].variant == wanted), None)
            if chosen is None:
                log.warning("[%s] synth variant %s not found", ctx.db_id, wanted)
                return
        else:
            # Never choose by gold metrics: that would leak labels into the model.
            # Prefer verified variants of the richest method kind, in config order.
            rank = {"llm": 0, "hybrid": 1, "heuristic": 2, "metanome": 3, "baseline": 4}
            chosen = min(
                results,
                key=lambda r: (not r[0].variant.endswith("+verify"), rank[r[0].method.kind]),
            )
        vr, props, _ = chosen
        accepted = [p for p in props if ctx.verdicts[p.key()].accepted]
        res = synthesize(
            ctx.evidence,
            accepted,
            ctx.verdicts,
            normal_form=self.cfg.synth.normal_form,
            source_schema=ctx.source_schema,
            target_schema=f"{ctx.source_schema}_normalized"[:63],
        )
        out = self.run_dir / "synth"
        (out / f"{ctx.db_id}.sql").write_text(res.ddl)
        (out / f"{ctx.db_id}.migration.sql").write_text(res.migration)
        (out / f"{ctx.db_id}.mmd").write_text(res.mermaid)
        (out / f"{ctx.db_id}.trace.json").write_text(
            json.dumps([t.model_dump() for t in res.trace], indent=2)
        )
        self.synth_index.append(
            {
                "db_id": ctx.db_id,
                "variant": vr.variant,
                "accepted": len(accepted),
                "skipped": len(res.skipped),
            }
        )

    # ------------------------------------------------------------------
    def _write_tables(self) -> None:
        rd = self.run_dir
        aggregates = self._aggregates()
        for name, rows in (
            ("metrics", self.metrics),
            ("costs", self.costs),
            ("hallucinations", self.halluc),
            ("aggregates", aggregates),
            ("synth", self.synth_index),
            ("learned", self.learned_rows),
        ):
            (rd / f"{name}.json").write_text(json.dumps(rows, indent=2))
            _write_csv(rd / f"{name}.csv", rows)

    def _aggregates(self) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for m in self.metrics:
            groups[(m["noise"], m["variant"], m["type"])].append(m)
        reference = next(
            (m["variant"] for m in self.metrics if m["kind"] == "baseline" and not m["verified"]),
            None,
        )
        rows = []
        for (noise, variant, ctype), ms in groups.items():
            cards = [
                TypeScore.model_validate({k: m[k] for k in TypeScore.model_fields if k in m})
                for m in ms
            ]
            agg = aggregate(cards, n_boot=self.cfg.eval.bootstrap, seed=self.cfg.seed)
            p_value = None
            if reference and variant != reference:
                ref = {m["dataset"]: m["f1"] for m in groups.get((noise, reference, ctype), [])}
                pairs: list[tuple[float, float]] = [
                    (m["f1"], ref[m["dataset"]])
                    for m in ms
                    if m["f1"] is not None and ref.get(m["dataset"]) is not None
                ]
                if pairs:
                    p_value = paired_permutation_test(
                        [a for a, _ in pairs], [b for _, b in pairs], seed=self.cfg.seed
                    )
            rows.append(
                {
                    "noise": noise,
                    "variant": variant,
                    "type": ctype,
                    "kind": ms[0]["kind"],
                    "verified": ms[0]["verified"],
                    **agg.model_dump(exclude={"type"}),
                    "reference": reference,
                    "p_vs_reference": p_value,
                }
            )
        return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("")
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _csv_value(v) for k, v in r.items()})


def _csv_value(v: Any) -> Any:
    if isinstance(v, list | tuple):
        return json.dumps(v)
    return v


def run_experiment(config: ExperimentConfig, run_dir: Path | None = None) -> Path:
    return Runner(config, run_dir).run()
