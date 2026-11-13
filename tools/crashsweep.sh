#!/bin/sh
# crashsweep.sh [frames] [missions...]: every mission's monkey replay (tools/replay/monkey_<M>.pad)
# played far past the gates' frames on the current build, one game at a time (replay_run.sh takes
# the pipeline lock). Per mission: exit code, the frame reached, and any crash or fatal lines.
# Report: /tmp/claude-0/crashsweep/report.txt (logs kept beside it).
frames=${1:-20000}; shift 2>/dev/null
T=/home/claude/avpe/tools
D=/tmp/claude-0/crashsweep; mkdir -p $D
ms="$*"; [ -n "$ms" ] || ms="M01 M02 M03 M05 M06 M09 M10 A01 A02 A03 A04 A06 A07 A08 P01 P02 P03 P04 P06 P08 P11"
: > $D/report.txt
for m in $ms; do
  pad=$T/replay/monkey_$m.pad
  [ -f $pad ] || { echo "$m: no pad" >> $D/report.txt; continue; }
  t0=$(date +%s)
  r=$(MISSION=$m AVPE_NOAPU=1 AVPE_REPLAY_PAD=$pad REPLAY_TIMEOUT=1500 sh $T/replay_run.sh $D/$m.rph $frames 2>&1 | tail -1)
  bad=$(grep -a -i -m3 -E "crash|fatal|exception|segfault|vbail|abort" $D/$m.rph.log | cut -c1-160 | tr '\n' '|')
  echo "$m: $r  ($(( $(date +%s) - t0 ))s)  ${bad:+BAD: $bad}" >> $D/report.txt
  tail -1 $D/report.txt
done
echo "done" >> $D/report.txt
