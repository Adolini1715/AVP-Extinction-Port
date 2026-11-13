#!/bin/sh
# regen_lifted.sh: regenerate every replay gate's all-lifted reference (replay/lifted_*.rph, gate_lifted_12000.rph)
# with the private seeded save dir of replay_run.sh. Old references go to replay/old_refs/<stamp>/.
# Resumable: a gate whose new file exists (and is non-empty) is skipped. One run at a time (pipeline lock).
R=/home/claude/avpe-recomp/replay; P=/home/claude/avpe/tools/replay; L=/home/claude/avpe-recomp/avpe_lifted
T=/home/claude/avpe/tools; mkdir -p $R/new_refs
run() { # name mission pad frames
  n=$1; [ -s $R/new_refs/$n.rph ] && { echo "$n: have"; return; }
  MISSION=$2 AVPE_REPLAY_PAD=$P/$3.pad sh $T/replay_run.sh $R/new_refs/$n.rph $4 $L >/dev/null
  [ -s $R/new_refs/$n.rph ] && echo "$n: done" || echo "$n: FAILED"; }
for t in T01 T02 T03 T04 T05; do l=$(echo $t | tr T t); n=6000; [ $t = T03 ] && n=9000; run lifted_${t}_pad $t $l $n; done
run lifted_PAUSE_pad M01 pause 4200
run lifted_M03_pad M03 m01 3000
run lifted_M05_pad M05 m01 3000
run lifted_M06_pad M06 m01 3000
run lifted_M09_pad M09 m01 3000
run lifted_M10_pad M10 m01 2600
run lifted_M02P_pad M02 m02 7300
run lifted_A01_pad A01 m01 6000
run lifted_P01_pad P01 m01 6000
run lifted_M02 M02 m01 2500
run gate_lifted_12000 M01 m01 12000
echo REGEN DONE
