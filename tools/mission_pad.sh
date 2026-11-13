#!/bin/sh
# mission_pad.sh <padfile> [log]: for verification runs that load a mission directly
# (AVPE_LEVEL=M01/background.tbd AVPE_LEVEL_AT=3): waits for that mission's briefing
# in the run log (default verify/last_multi_run.log; delete it first), closes it
# with START, then works the sticks and buttons so input paths run.
P=$1; L=${2:-/home/claude/avpe-recomp/verify/last_multi_run.log}; : > $P
sleep 5
while ! grep -aq "replaced with" $L 2>/dev/null; do sleep 2; done
while [ $(sed -n "/replaced with/,$ p" $L | grep -ac "mission goals up") -lt 1 ]; do sleep 2; done; sleep 3
echo "start" >> $P; sleep 3; echo "a" >> $P; sleep 3; echo "start" >> $P; sleep 6
for r in 1 2 3; do
  for b in lleft:800 lup:800 lright:800 ldown:800 rleft:800 rright:800 rup:600 rdown:600 black white x y b back lthumb rthumb lt:500 rt:500; do
    echo "$b" >> $P; sleep 2
  done
done
