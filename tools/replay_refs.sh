#!/bin/sh
# replay_refs.sh [names...]: regenerate the all-lifted reference runs the replay gates compare
# against (pipe.sh replayall), with the SAME mission, pad and frame counts. Needed whenever a
# change to the bridge or the platform layer changes what the lifted and the hybrid build both do
# (a native replacement of library code moves heap and allocator state in both). Builds the
# lifted binary first (tools/build_lifted.sh). Names: T01..T05 PAUSE M03 M05 M06 M09 M10 M02P A01 P01 M02 M01
# (default: all). The hybrid must still match them page for page: pipe.sh replayall.
T=/home/claude/avpe/tools; R=/home/claude/avpe-recomp/replay; P=$T/replay; L=/home/claude/avpe-recomp/avpe_lifted
export AVPE_LOCKED=1; exec 9>/tmp/avpe-pipeline.lock; flock 9
sh $T/build_lifted.sh > /tmp/build_lifted.log 2>&1; tail -3 /tmp/build_lifted.log; [ -x $L ] || { echo "lifted build failed: no $L"; exit 1; }
want="$*"; [ -n "$want" ] || want="T01 T02 T03 T04 T05 PAUSE M03 M05 M06 M09 M10 M02P A01 P01 M02 M01"
for n in $want; do
  case $n in
    T01|T02|T04|T05) ms=$n; pd=$(echo $n | tr T t); fr=6000; out=$R/lifted_${n}_pad.rph ;;
    T03) ms=T03; pd=t03; fr=9000; out=$R/lifted_T03_pad.rph ;;
    PAUSE) ms=M01; pd=pause; fr=4200; out=$R/lifted_PAUSE_pad.rph ;;
    M03|M05|M06|M09) ms=$n; pd=m01; fr=3000; out=$R/lifted_${n}_pad.rph ;;
    M10) ms=M10; pd=m01; fr=2600; out=$R/lifted_M10_pad.rph ;;
    M02P) ms=M02; pd=m02; fr=7300; out=$R/lifted_M02P_pad.rph ;;
    A01|P01) ms=$n; pd=m01; fr=6000; out=$R/lifted_${n}_pad.rph ;;
    M02) ms=M02; pd=m01; fr=2500; out=$R/lifted_M02.rph; unset AVPE_REPLAY_PAD ;;
    M01) ms=M01; pd=m01; fr=12000; out=$R/gate_lifted_12000.rph; unset AVPE_REPLAY_PAD ;;
    *) echo "unknown $n"; continue ;;
  esac
  echo "== $n"
  if [ $n = M02 ] || [ $n = M01 ]; then MISSION=$ms sh $T/replay_run.sh $out $fr $L
  else MISSION=$ms AVPE_REPLAY_PAD=$P/$pd.pad sh $T/replay_run.sh $out $fr $L; fi
done
