"""Command-line interface: ``srev <command>`` (or ``python -m srev``)."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Any

from srev import __version__


def _db_args(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dsn", help="PostgreSQL DSN (default: $SREV_DSN)")
    g.add_argument("--sqlite", help="SQLite database file")
    p.add_argument("--schema", default="public", help="PostgreSQL schema (default: public)")


def _connect(args: argparse.Namespace) -> Any:
    from srev.db import connect

    return connect(dsn=args.dsn, sqlite=args.sqlite, schema=args.schema)


def _write(path: str | None, text: str) -> None:
    if path in (None, "-"):
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text)
        print(f"wrote {path}", file=sys.stderr)


def _load_proposals(path: str) -> list[Any]:
    from srev.model import Proposal

    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get("proposals", data.get("labels", []))
    out = []
    for item in data:
        if "proposal" in item:  # verified proposals file
            item = item["proposal"]
        out.append(Proposal.model_validate(item))
    return out


# ---------------------------------------------------------------- commands


def cmd_datasets_list(args: argparse.Namespace) -> int:
    from srev.datasets import REGISTRY

    for name, (_, doc) in sorted(REGISTRY.items()):
        first = doc.strip().splitlines()[0] if doc.strip() else ""
        print(f"{name:15} {first}")
    return 0


def cmd_datasets_spider(args: argparse.Namespace) -> int:
    from srev.datasets.spider import list_databases

    dbs = list_databases(args.root, args.tables_json)
    print(f"{'db_id':32} tables  PKs  FKs")
    for d in dbs:
        print(f"{d['db_id']:32} {d['tables']:6} {d['primary_keys']:4} {d['foreign_keys']:4}")
    print(
        f"{len(dbs)} databases, {sum(d['foreign_keys'] for d in dbs)} annotated FKs",
        file=sys.stderr,
    )
    return 0


def cmd_datasets_load(args: argparse.Namespace) -> int:
    from srev.datasets import load_dataset
    from srev.db import materialize
    from srev.noise import inject, resolve_profile
    from srev.truth import gold_from_dataset

    params = {"scale": args.scale} if args.scale is not None else {}
    ds = load_dataset(args.name, seed=args.seed, **params)
    gold = gold_from_dataset(ds)
    if args.noise not in (None, "0", "none"):
        ds, noise_log = inject(ds, resolve_profile(args.noise), seed=args.seed, gold=gold)
        gold = noise_log.renames.gold(gold)
        if args.noise_log:
            _write(args.noise_log, noise_log.model_dump_json(indent=2))
    with _connect(args) as conn:
        materialize(ds, conn, with_constraints=args.with_constraints)
        print(
            f"loaded {ds.name}: {len(ds.tables)} tables, {ds.n_rows} rows into {conn.describe()}",
            file=sys.stderr,
        )
    if args.gold:
        gold.save(args.gold)
    if args.workload:
        _write(args.workload, ";\n\n".join(ds.queries) + ";\n")
    return 0


def cmd_ingest_csv(args: argparse.Namespace) -> int:
    from srev.db import materialize
    from srev.ingest import dataset_from_csv

    ds = dataset_from_csv(
        args.paths,
        args.name,
        delimiter=args.delimiter,
        encoding=args.encoding,
        max_rows=args.max_rows,
    )
    with _connect(args) as conn:
        materialize(ds, conn)
        print(
            f"loaded {len(ds.tables)} tables, {ds.n_rows} rows into {conn.describe()}",
            file=sys.stderr,
        )
    for t in ds.tables:
        cols = ", ".join(f"{c.name} {c.type}" for c in t.columns)
        print(f"{t.name} ({len(t.rows)} rows): {cols}")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from srev.db import SQLiteConnector, materialize
    from srev.discover import DiscoverConfig, discover
    from srev.extract import extract
    from srev.ingest import dataset_from_csv
    from srev.qlog import load_workload, mine_joins
    from srev.synth import synthesize

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    if args.csv:
        ds = dataset_from_csv(args.csv, args.name or "csv", delimiter=args.delimiter)
        db_path = out / "data.sqlite"
        db_path.unlink(missing_ok=True)
        conn = SQLiteConnector(db_path)
        materialize(ds, conn)
        print(f"loaded {len(ds.tables)} CSV tables into {db_path}", file=sys.stderr)
    else:
        conn = _connect(args)
    cfg = DiscoverConfig(fk_threshold=args.fk_threshold)
    with conn:
        name = args.name or (args.schema if conn.dialect == "postgres" else "database")
        joins = None
        if args.workload:
            schema_map = {t: [c.name for c in conn.list_columns(t)] for t in conn.list_tables()}
            joins = mine_joins(load_workload(args.workload), schema_map)
        result = discover(conn, name, joins=joins, config=cfg)
        evidence = extract(conn, name, rows_per_table=20)
        source_schema = conn.schema if conn.dialect == "postgres" else "main"  # type: ignore[attr-defined]
    evidence.save(out / "evidence.json")
    result.save(out / "constraints.json")
    for label, items in (("review", result.review), ("rejected", result.rejected)):
        (out / f"{label}.json").write_text(json.dumps([v.model_dump() for v in items], indent=2))
    verdicts = {v.proposal.key(): v.verdict for v in result.constraints}
    synth = synthesize(
        evidence,
        result.accepted(),
        verdicts,
        source_schema=source_schema,
        target_schema=args.target_schema,
    )
    (out / "model.sql").write_text(synth.ddl)
    (out / "migration.sql").write_text(synth.migration)
    (out / "model.mmd").write_text(synth.mermaid)
    print(f"{'type':4} {'constraint':70} {'conf':>5} statistic")
    for v in result.constraints:
        p = v.proposal
        print(
            f"{p.type:4} {p.describe()[len(p.type) + 1 :]:70} {p.confidence:5.2f} "
            f"{v.verdict.statistic}={v.verdict.value}"
        )
    for v in result.review:
        print(f"?FK {v.proposal.describe()[3:]:71} {v.proposal.confidence:5.2f} needs review")
    c = result.counts()
    print(
        f"accepted FK {c['FK']}, UCC {c['UCC']}, FD {c['FD']}; {len(result.review)} to review, "
        f"{len(result.rejected)} rejected; {result.seconds}s -> {out}/",
        file=sys.stderr,
    )
    return 0


def cmd_truth_export(args: argparse.Namespace) -> int:
    from srev.truth import gold_from_connector

    with _connect(args) as conn:
        gold = gold_from_connector(conn, args.name or args.schema)
    _write(args.output, gold.model_dump_json(indent=2))
    print(f"exported {gold.counts()}", file=sys.stderr)
    return 0


def cmd_truth_strip(args: argparse.Namespace) -> int:
    from srev.truth import strip_constraints

    with _connect(args) as conn:
        tables = strip_constraints(conn, args.target)
    print(
        f"copied {len(tables)} tables into schema {args.target} without constraints",
        file=sys.stderr,
    )
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    from srev.extract import extract

    with _connect(args) as conn:
        ev = extract(
            conn,
            args.name or args.schema,
            rows_per_table=args.rows,
            top_k=args.top_k,
            seed=args.seed,
            include_comments=not args.no_comments,
        )
    _write(args.output, ev.model_dump_json(indent=2))
    return 0


def cmd_qlog(args: argparse.Namespace) -> int:
    from srev.extract import Evidence
    from srev.qlog import load_workload, mine_joins

    schema = Evidence.load(args.evidence).schema_map() if args.evidence else None
    stats = mine_joins(load_workload(args.workload), schema)
    _write(args.output, stats.model_dump_json(indent=2))
    print(
        f"{stats.n_parsed}/{stats.n_queries} statements parsed, {len(stats.pairs)} join pairs, "
        f"{stats.n_failed} failures",
        file=sys.stderr,
    )
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    from srev.extract import Evidence
    from srev.llm import ChatClient, HeuristicProposer, LLMConfig, LLMProposer, PromptConfig
    from srev.qlog import JoinStats

    ev = Evidence.load(args.evidence)
    joins = JoinStats.model_validate_json(Path(args.joins).read_text()) if args.joins else None
    channels = args.channels.split(",")
    if args.heuristic:
        out = HeuristicProposer(channels).propose(ev, joins)
    else:
        cfg = LLMConfig(
            base_url=args.base_url,
            model=args.model,
            temperature=args.temperature,
            seed=args.seed,
            cache_dir=args.cache_dir,
            response_format=args.response_format,
        )
        client = ChatClient(cfg)
        try:
            pc = PromptConfig(
                channels=channels, token_budget=args.token_budget, paraphrase=args.paraphrase
            )
            out = LLMProposer(client, pc).propose(ev, joins)
        finally:
            client.close()
        if args.save_prompt and out.prompts:
            p = out.prompts[0]
            _write(args.save_prompt, f"### SYSTEM\n{p.system}\n\n### USER\n{p.user}\n")
    payload = {
        "proposals": [p.model_dump() for p in out.proposals],
        "parse": out.parse.model_dump(),
        "seconds": out.seconds,
    }
    _write(args.output, json.dumps(payload, indent=2))
    print(
        f"{len(out.proposals)} proposals; parse failures: {out.parse.response_failures} "
        f"responses, {out.parse.item_failures}/{out.parse.items} items",
        file=sys.stderr,
    )
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    from srev.baselines import ProfilingConfig, profile

    cfg = ProfilingConfig(ucc_max_arity=args.ucc_max_arity, fd_max_lhs=args.fd_max_lhs)
    with _connect(args) as conn:
        res = profile(conn, cfg)
    payload = {
        "proposals": [p.model_dump() for p in res.proposals],
        "stats": res.stats,
        "seconds": res.seconds,
    }
    _write(args.output, json.dumps(payload, indent=2))
    print(f"profiled in {res.seconds}s: {res.stats}", file=sys.stderr)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from srev.verify import Verifier, VerifyConfig

    props = _load_proposals(args.proposals)
    cfg = VerifyConfig(
        fk_min_coverage=args.fk_min_coverage,
        ucc_min_uniqueness=args.ucc_min_uniqueness,
        fd_max_g3=args.fd_max_g3,
    )
    with _connect(args) as conn:
        verified = Verifier(conn, cfg).verify_all(props)
    _write(args.output, json.dumps([v.model_dump() for v in verified], indent=2))
    n_ok = sum(v.verdict.accepted for v in verified)
    print(f"accepted {n_ok}/{len(verified)}", file=sys.stderr)
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from srev.eval import analyse, score
    from srev.extract import Evidence
    from srev.model import Verdict
    from srev.truth import GoldSet

    props = _load_proposals(args.proposals)
    gold = GoldSet.load(args.gold)
    verdicts: dict[Any, Verdict] = {}
    if args.proposals_are_verified or args.accepted_only:
        raw = json.loads(Path(args.proposals).read_text())
        from srev.model import VerifiedProposal

        vps = [VerifiedProposal.model_validate(r) for r in raw]
        verdicts = {v.proposal.key(): v.verdict for v in vps}
        if args.accepted_only:
            props = [v.proposal for v in vps if v.verdict.accepted]
    card = score(props, gold.labels, n_boot=args.bootstrap, top_k_factor=args.top_k_factor)
    result: dict[str, Any] = {
        "scores": {k: v.model_dump() for k, v in card.scores.items()},
        "top_k": {k: v.model_dump() for k, v in card.top_k.items()},
    }
    if args.evidence:
        ev = Evidence.load(args.evidence)
        hr = analyse(props, gold.labels, ev.has_ref, verdicts)
        result["hallucinations"] = hr.model_dump(exclude={"items"})
    _write(args.output, json.dumps(result, indent=2))
    for t, s in card.scores.items():
        print(
            f"{t:4} P={s.precision} R={s.recall} F1={s.f1} (TP {s.tp}, FP {s.fp}, FN {s.fn})",
            file=sys.stderr,
        )
    return 0


def cmd_synth(args: argparse.Namespace) -> int:
    from srev.extract import Evidence
    from srev.model import VerifiedProposal
    from srev.synth import synthesize

    ev = Evidence.load(args.evidence)
    raw = json.loads(Path(args.proposals).read_text())
    verdicts = {}
    if isinstance(raw, list) and raw and "verdict" in raw[0]:
        vps = [VerifiedProposal.model_validate(r) for r in raw]
        props = [v.proposal for v in vps if v.verdict.accepted]
        verdicts = {v.proposal.key(): v.verdict for v in vps}
    else:
        props = _load_proposals(args.proposals)
    res = synthesize(
        ev,
        props,
        verdicts,
        normal_form=args.normal_form,
        source_schema=args.source_schema,
        target_schema=args.target_schema,
    )
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "model.sql").write_text(res.ddl)
    (out / "migration.sql").write_text(res.migration)
    (out / "model.mmd").write_text(res.mermaid)
    (out / "trace.json").write_text(json.dumps([t.model_dump() for t in res.trace], indent=2))
    print(f"wrote {out}/model.sql, migration.sql, model.mmd, trace.json", file=sys.stderr)
    for s in res.skipped:
        print(f"skipped: {s}", file=sys.stderr)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from srev.config import load_config
    from srev.runner import run_experiment

    cfg = load_config(args.config)
    if args.output_dir:
        cfg.output_dir = args.output_dir
    run_dir = run_experiment(cfg, Path(args.run_dir) if args.run_dir else None)
    print(run_dir)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from srev.report import write_report

    print(write_report(args.run_dir))
    return 0


def compare_metrics(a: Path, b: Path, tolerance: float) -> list[str]:
    keys = ("tp", "fp", "fn", "precision", "recall", "f1")
    load = {p: json.loads((p / "metrics.json").read_text()) for p in (a, b)}
    index = {(m["db_id"], m["variant"], m["type"]): m for m in load[b]}
    diffs = []
    for m in load[a]:
        other = index.get((m["db_id"], m["variant"], m["type"]))
        if other is None:
            diffs.append(f"missing in reproduction: {m['db_id']} {m['variant']} {m['type']}")
            continue
        for k in keys:
            x, y = m[k], other[k]
            if (x is None) != (y is None) or (x is not None and abs(x - y) > tolerance):
                diffs.append(f"{m['db_id']} {m['variant']} {m['type']} {k}: {x} -> {y}")
    return diffs


def cmd_reproduce(args: argparse.Namespace) -> int:
    import yaml

    from srev.config import ExperimentConfig
    from srev.runner import run_experiment

    src = Path(args.run_dir)
    cfg = ExperimentConfig.model_validate(yaml.safe_load((src / "config.yaml").read_text()))
    if args.replay:
        cache = str((src / "llm_cache").resolve())
        cfg.llm = cfg.llm.model_copy(
            update={"cache_dir": cfg.llm.cache_dir or cache, "offline": True}
        )
        for m in cfg.methods:
            if m.llm:
                m.llm = m.llm.model_copy(
                    update={"cache_dir": m.llm.cache_dir or cache, "offline": True}
                )
    out = Path(args.output) if args.output else src.parent / f"{src.name}_repro"
    new = run_experiment(cfg, out)
    diffs = compare_metrics(src, new, args.tolerance)
    if diffs:
        print(f"{len(diffs)} metric differences (tolerance {args.tolerance}):")
        for d in diffs[:50]:
            print("  " + d)
        return 1
    print(f"reproduced: all metrics match within {args.tolerance} ({new})")
    return 0


def cmd_export_latex(args: argparse.Namespace) -> int:
    from srev.export import latex_tables

    text = latex_tables(
        args.run_dir,
        metric=args.metric,
        types=tuple(t.strip().upper() for t in args.types.split(",")),
        variants=args.variants.split(",") if args.variants else None,
        with_ci=args.ci,
    )
    _write(args.output, text)
    return 0


def cmd_kappa(args: argparse.Namespace) -> int:
    from srev.eval import cohen_kappa

    def read(path: str) -> dict[str, str]:
        with open(path, newline="") as fh:
            return {r["id"]: r["category"] for r in csv.DictReader(fh)}

    a, b = read(args.a), read(args.b)
    common = sorted(set(a) & set(b))
    kappa = cohen_kappa([a[i] for i in common], [b[i] for i in common])
    print(json.dumps({"items": len(common), "kappa": kappa}))
    return 0


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="srev", description=__doc__)
    parser.add_argument("--version", action="version", version=f"srev {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    ds = sub.add_parser("datasets", help="built-in benchmark datasets")
    ds_sub = ds.add_subparsers(dest="action", required=True)
    ds_sub.add_parser("list", help="list datasets").set_defaults(func=cmd_datasets_list)
    p = ds_sub.add_parser("spider", help="list databases of a Spider/BIRD-style benchmark")
    p.add_argument("root", help="benchmark directory containing tables.json")
    p.add_argument("--tables-json", help="annotation file name (BIRD: dev_tables.json)")
    p.set_defaults(func=cmd_datasets_spider)
    p = ds_sub.add_parser("load", help="generate a dataset and load it into a database")
    p.add_argument("name")
    _db_args(p)
    p.add_argument("--scale", type=float)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--with-constraints",
        action="store_true",
        help="declare PK/UNIQUE/FK (default: load without constraints)",
    )
    p.add_argument("--noise", help="noise profile: none|low|medium|high or 0-3")
    p.add_argument("--noise-log", help="write the noise log (renames, perturbations) here")
    p.add_argument("--gold", help="write gold labels (renamed if noise is applied) here")
    p.add_argument("--workload", help="write the dataset's query workload (.sql) here")
    p.set_defaults(func=cmd_datasets_load)

    ig = sub.add_parser("ingest", help="load external data (CSV) into a database")
    ig_sub = ig.add_subparsers(dest="action", required=True)
    p = ig_sub.add_parser("csv", help="CSV files or folders -> one table per file")
    p.add_argument("paths", nargs="+", help="CSV files and/or folders of *.csv")
    _db_args(p)
    p.add_argument("--name", default="csv", help="dataset label")
    p.add_argument("--delimiter", help="default: sniffed from the file")
    p.add_argument("--encoding", default="utf-8")
    p.add_argument("--max-rows", type=int)
    p.set_defaults(func=cmd_ingest_csv)

    p = sub.add_parser("discover", help="one-shot discovery of keys, FKs and FDs")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--dsn", help="PostgreSQL DSN (default: $SREV_DSN)")
    src.add_argument("--sqlite", help="SQLite database file")
    src.add_argument("--csv", nargs="+", help="CSV files or folders (loaded into data.sqlite)")
    p.add_argument("--schema", default="public")
    p.add_argument("--delimiter")
    p.add_argument("--workload", help="optional query log: enables the join-log feature")
    p.add_argument("--name")
    p.add_argument("--fk-threshold", type=float, default=0.5)
    p.add_argument("--target-schema", default="normalized")
    p.add_argument("-o", "--output", default="discovery")
    p.set_defaults(func=cmd_discover)

    tr = sub.add_parser("truth", help="ground-truth export and constraint stripping")
    tr_sub = tr.add_subparsers(dest="action", required=True)
    p = tr_sub.add_parser("export", help="export declared constraints as gold labels")
    _db_args(p)
    p.add_argument("--name", help="database label stored in the gold file")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_truth_export)
    p = tr_sub.add_parser("strip", help="copy a PostgreSQL schema without constraints")
    _db_args(p)
    p.add_argument("--target", required=True, help="target schema")
    p.set_defaults(func=cmd_truth_strip)

    p = sub.add_parser("extract", help="extract schema, statistics and samples as evidence JSON")
    _db_args(p)
    p.add_argument("--name")
    p.add_argument("--rows", type=int, default=50)
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--no-comments", action="store_true", help="omit table/column comments")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("qlog", help="mine equi-join predicates from a query log")
    p.add_argument("workload", help=".sql, pg_stat_statements .csv or .jsonl")
    p.add_argument("--evidence", help="evidence JSON, used to resolve unqualified columns")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_qlog)

    p = sub.add_parser("propose", help="propose constraints with an LLM or the heuristic")
    p.add_argument("--evidence", required=True)
    p.add_argument("--joins")
    p.add_argument("--channels", default="names,stats,samples,logs")
    p.add_argument("--heuristic", action="store_true", help="offline name/statistics heuristic")
    p.add_argument("--base-url", default="http://localhost:8000/v1")
    p.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--paraphrase", type=int, default=0)
    p.add_argument("--token-budget", type=int, default=8000)
    p.add_argument(
        "--response-format", default="json_schema", choices=["json_schema", "json_object", "none"]
    )
    p.add_argument("--cache-dir")
    p.add_argument("--save-prompt")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("profile", help="run the exact profiling baseline")
    _db_args(p)
    p.add_argument("--ucc-max-arity", type=int, default=3)
    p.add_argument("--fd-max-lhs", type=int, default=2)
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("verify", help="check proposals against the data with SQL")
    p.add_argument("--proposals", required=True)
    _db_args(p)
    p.add_argument("--fk-min-coverage", type=float, default=0.95)
    p.add_argument("--ucc-min-uniqueness", type=float, default=0.99)
    p.add_argument("--fd-max-g3", type=float, default=0.05)
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("evaluate", help="score proposals against gold labels")
    p.add_argument("--proposals", required=True)
    p.add_argument("--gold", required=True)
    p.add_argument("--evidence", help="enables the hallucination analysis")
    p.add_argument(
        "--proposals-are-verified",
        action="store_true",
        help="proposals file is `srev verify` output (use its verdicts)",
    )
    p.add_argument(
        "--accepted-only",
        action="store_true",
        help="score only proposals the verifier accepted (implies verified input)",
    )
    p.add_argument("--bootstrap", type=int, default=1000)
    p.add_argument("--top-k-factor", type=float, default=1.0)
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("synth", help="synthesise normalised DDL and an ER diagram")
    p.add_argument("--evidence", required=True)
    p.add_argument("--proposals", required=True, help="proposals or `srev verify` output")
    p.add_argument("--normal-form", choices=["bcnf", "none"], default="bcnf")
    p.add_argument("--source-schema", default="public")
    p.add_argument("--target-schema", default="normalized")
    p.add_argument("-o", "--output", default="synth")
    p.set_defaults(func=cmd_synth)

    p = sub.add_parser("run", help="run an experiment from a YAML config")
    p.add_argument("--config", required=True)
    p.add_argument("--output-dir")
    p.add_argument("--run-dir", help="exact output directory (default: <output_dir>/<run id>)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="regenerate report.md for a run directory")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("reproduce", help="re-run a stored run and compare metrics")
    p.add_argument("run_dir")
    p.add_argument(
        "--replay",
        action="store_true",
        help="replay cached LLM responses offline instead of calling the server",
    )
    p.add_argument("--tolerance", type=float, default=0.0)
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_reproduce)

    ex = sub.add_parser("export", help="paper-ready exports of a run")
    ex_sub = ex.add_subparsers(dest="action", required=True)
    p = ex_sub.add_parser("latex", help="booktabs tables of aggregate scores")
    p.add_argument("run_dir")
    p.add_argument(
        "--metric",
        default="macro_f1",
        choices=["macro_f1", "micro_f1", "micro_precision", "micro_recall"],
    )
    p.add_argument("--types", default="FK,UCC,FD")
    p.add_argument("--variants", help="comma-separated variants in the desired row order")
    p.add_argument("--ci", action="store_true", help="add bootstrap CIs (macro F1 only)")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_export_latex)

    au = sub.add_parser("audit", help="manual-audit helpers")
    au_sub = au.add_subparsers(dest="action", required=True)
    p = au_sub.add_parser("kappa", help="Cohen's kappa between two annotators' CSV labels")
    p.add_argument("a", help="CSV with columns id,category")
    p.add_argument("b")
    p.set_defaults(func=cmd_kappa)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
