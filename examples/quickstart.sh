#!/usr/bin/env bash
# End-to-end CLI walkthrough on SQLite: no server and no GPU needed.
# Usage: bash examples/quickstart.sh [output-dir]
set -euo pipefail

out="${1:-quickstart-out}"
mkdir -p "$out"
db="$out/university.sqlite"

# 1. A noisy, constraint-free working copy plus its gold labels and workload
srev datasets load university --sqlite "$db" --noise medium \
    --gold "$out/gold.json" --workload "$out/workload.sql" --noise-log "$out/noise.json"

# 2. Evidence: schema, statistics, samples and mined join predicates
srev extract --sqlite "$db" --name university --rows 20 -o "$out/evidence.json"
srev qlog "$out/workload.sql" --evidence "$out/evidence.json" -o "$out/joins.json"

# 3. Proposals: offline heuristic and the exact profiling baseline.
#    With an LLM server, replace --heuristic by --base-url/--model.
srev propose --evidence "$out/evidence.json" --joins "$out/joins.json" --heuristic \
    -o "$out/proposals.json"
srev profile --sqlite "$db" -o "$out/baseline.json"

# 4. Verify against the data, evaluate against gold, synthesise a model
srev verify --proposals "$out/proposals.json" --sqlite "$db" -o "$out/verified.json"
srev evaluate --proposals "$out/verified.json" --proposals-are-verified --gold "$out/gold.json" \
    --evidence "$out/evidence.json" -o "$out/eval_raw.json"
srev evaluate --proposals "$out/verified.json" --accepted-only --gold "$out/gold.json" \
    --evidence "$out/evidence.json" -o "$out/eval_verified.json"
srev evaluate --proposals "$out/baseline.json" --gold "$out/gold.json" -o "$out/eval_baseline.json"
srev synth --evidence "$out/evidence.json" --proposals "$out/verified.json" -o "$out/synth"

echo "done: see $out/"
