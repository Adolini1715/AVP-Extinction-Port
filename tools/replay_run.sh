#!/bin/sh
# replay_run.sh <out.rph> [frames] [binary]: one deterministic replay run in
# M01 (notes/replay.md): fixed clock, scripted pad (tools/replay/m01.pad),
# page hashes every AVPE_REPLAY_EVERY frames into <out.rph>, log in <out>.log.
# Holds the pipeline lock like pipe.sh. MISSION=M02 and AVPE_REPLAY_PAD,
# AVPE_REPLAY_EVERY, AVPE_RUNNER (command prefix, e.g. gdb -batch -x s.py --args), AVPE_LOCKED (caller holds the lock),
# AVPE_REPLAY_EVERY, AVPE_REPLAY_SNAP pass through.
out=$1; frames=${2:-3000}; bin=${3:-/home/claude/avpe-recomp/avpe_recomp}
[ -n "$AVPE_LOCKED" ] || { exec 9>/tmp/avpe-pipeline.lock; flock 9; }
cd /home/claude/avpe-recomp
# AVPE_NO_LEVEL=1: no level substitution, the run plays the front end (title, menus) from the pad
LV="AVPE_LEVEL=${MISSION:-M01}/background.tbd AVPE_LEVEL_AT=3"; [ -n "$AVPE_NO_LEVEL" ] && LV="AVPE_NOLEVEL=1"
rm -f "$out"
# front-end runs read and write profiles and saves: give each run a private copy of the seed
# (AVPE_SAVE_SEED, default none = empty), so hybrid and lifted start from the same disk
# every run (level or front end) gets a private save dir copied from a fixed seed (default tools/replay/fe_seed1),
# so probes, sweeps and playtests that write into the default saves/ can never change the gates
rm -rf "$out.saves"; mkdir -p "$out.saves"
cp -a "${AVPE_SAVE_SEED:-/home/claude/avpe/tools/replay/fe_seed1}/." "$out.saves/"; export AVPE_SAVE_DIR="$out.saves"
if [ -n "$AVPE_NO_LEVEL" ]; then
  # 0x2F69AC = free 16 KB blocks on the save disk, read from the host file system: not reproducible
  export AVPE_REPLAY_EXCLUDE=${AVPE_REPLAY_EXCLUDE-740020-740024,2E01E2-2E01E6,2F69AC-2F69B0}; fi
DISPLAY=:99 AVPE_NO_GPU=${AVPE_NO_GPU-1} AVPE_NOVSYNC=1 AVPE_FATAL_EXIT=${AVPE_FATAL_EXIT-1} AVPE_REPLAY=fixed \
AVPE_REPLAY_PAD=${AVPE_REPLAY_PAD:-/home/claude/avpe/tools/replay/m01.pad} \
AVPE_REPLAY_OUT="$out" AVPE_REPLAY_FRAMES=$frames \
env $LV \
timeout -k 10 ${REPLAY_TIMEOUT:-900} $AVPE_RUNNER "$bin" > /dev/null 2> "$out.log"
rc=$?; rm -rf "$out.saves"
echo "exit $rc frames $(grep -a '\[REPLAY\] done' "$out.log" || tail -2 "$out.log")"
