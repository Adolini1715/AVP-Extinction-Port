#!/bin/sh
# Fresh native run into Marine mission 1 (no GPU, fresh save dir).
# Usage: m01_run.sh <tag> [frames]   waits until the mission reaches <frames>
# swaps (default 4000) or the process dies; prints OK/CRASH. Leaves it running.
t=$1; want=${2:-4000}; cd /home/claude/avpe-recomp
pkill -x avpe_recomp; sleep 1
sd=/home/claude/avpe-recomp/rs_${t}_$(date +%s); mkdir -p $sd; : > pad_$t.txt
(DISPLAY=:99 AVPE_NO_GPU=1 AVPE_NOVSYNC=1 AVPE_FATAL_EXIT=1 AVPE_SAVE_DIR=$sd AVPE_PAD=/home/claude/avpe-recomp/pad_$t.txt nohup ./avpe_recomp > run_$t.log 2>&1 &)
sleep 2
( timeout 600 sh /home/claude/avpe/recomp/nav_m01.sh run_$t.log pad_$t.txt >/dev/null & )
while pgrep -x avpe_recomp >/dev/null; do
  n=$(grep -o 'Swap #[0-9]*' run_$t.log | tail -1 | tr -dc 0-9)
  [ "${n:-0}" -ge "$want" ] && { echo "OK $t frame $n"; exit 0; }
  sleep 3
done
echo "CRASH $t at $(grep -o 'Swap #[0-9]*' run_$t.log | tail -1)"; exit 1
