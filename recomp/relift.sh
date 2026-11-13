#!/bin/sh
# Full re-lift: disassemble every code section with the structural and observed
# seeds, identify, recover ABIs, apply recovered names, then rebuild.
set -e
AVPE=/home/claude/avpe; XR=/home/claude/xboxrecomp; XBE=/home/claude/avpe-recomp/game_files/default.xbe
cd $XR
SEEDS="--seed-functions $AVPE/recomp/seeds_structural.json"
[ -f $AVPE/recomp/seeds_observed.json ] && SEEDS="$SEEDS --seed-functions $AVPE/recomp/seeds_observed.json"
# callbacks only ever passed as constants (tools/seed_pointers.py)
[ -f $AVPE/recomp/seeds_pointers.json ] && SEEDS="$SEEDS --seed-functions $AVPE/recomp/seeds_pointers.json"
python3 -m tools.disasm $XBE $SEEDS --force -v > /tmp/claude-0/disasm.log 2>&1; grep -E "Seed file|Total functions" /tmp/claude-0/disasm.log || true
python3 -m tools.func_id $XBE > /dev/null
python3 -m tools.abi_analysis $XBE 2>&1 | tail -1
rm -f tools/disasm/output/functions.json.orig
python3 -I $AVPE/tools/apply_names.py $AVPE/symbols/xbox_symbols.csv tools/disasm/output/functions.json
$AVPE/recomp/rebuild.sh
python3 -I -c "import json; s=json.load(open('$XR/tools/recomp/output/summary.json')); print({k:s[k] for k in ('total','translated','failed','unresolved_stubs')})"
