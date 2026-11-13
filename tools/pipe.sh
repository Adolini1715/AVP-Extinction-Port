#!/bin/sh
# pipe.sh <command...>: run a build or verification step holding the pipeline
# lock, so several workers can share one build tree and one game binary.
#   pipe.sh rebuild                         recomp/rebuild.sh
#   pipe.sh multi <verify_multi.py args>    a verify_multi run (boot / attract)
#   pipe.sh mission <verify_multi.py args>  the same in Marine 1 (AVPE_LEVEL), briefing closed by pad
#   pipe.sh single <verify.py args>         one verify.py run (boot / attract)
#   pipe.sh msingle <verify.py args>        verify.py in Marine 1
#   pipe.sh replayall                       all gates: M01 12000, M02 2500, T01/T02/T04/T05 6000, T03 9000, PAUSE 4200, M03/M05/M06/M09 3000, M10 2600, M02P (m02.pad) 7300, A01/P01 (m01.pad) 6000 (notes/wave1.md)
#   pipe.sh replay [--lifted|--rebuild-lifted] [frames]
#                                           replay gate (notes/replay.md): hybrid build vs cached all-lifted
#                                           hashes, stack mask, exit 1 on any differing page. --lifted redoes
#                                           the cached lifted run, --rebuild-lifted also reruns build_lifted.sh.
# MISSION=M02 (env) makes mission/msingle load that mission instead of M01.
# Logs go to stdout. Waits for the lock (other workers' steps) first.
T=/home/claude/avpe/tools; V=/home/claude/avpe-recomp/verify
exec 9>/tmp/avpe-pipeline.lock
flock 9
what=$1; shift
pad=/tmp/avpe-pad.$$.txt
feed() { rm -f "$1"; sh $T/mission_pad.sh $pad "$1" & fp=$!; }
unfeed() { kill $fp 2>/dev/null; pkill -P $fp 2>/dev/null; rm -f $pad; }
cd /home/claude/avpe
case $what in
  rebuild) sh recomp/rebuild.sh 2>&1 | grep -iv "^warning" | tail -15 ;;
  multi)   python3 $T/verify_multi.py "$@" ;;
  single)  python3 $T/verify.py "$@" ;;
  mission) feed $V/last_multi_run.log
           AVPE_LEVEL=${MISSION:-M01}/background.tbd AVPE_LEVEL_AT=3 python3 $T/verify_multi.py --pad $pad "$@"; unfeed ;;
  msingle) feed $V/last_run.log
           AVPE_LEVEL=${MISSION:-M01}/background.tbd AVPE_LEVEL_AT=3 AVPE_PAD=$pad VERIFY_TIMEOUT=${VERIFY_TIMEOUT:-200} python3 $T/verify.py "$@"; unfeed ;;
  replay)  R=/home/claude/avpe-recomp/replay; L=/home/claude/avpe-recomp/avpe_lifted; force=0; frames=3000
           for a in "$@"; do case $a in
             --lifted) force=1 ;; --rebuild-lifted) force=2 ;; [0-9]*) frames=$a ;; esac; done
           mkdir -p $R; export AVPE_LOCKED=1
           [ $force = 2 ] && { sh $T/build_lifted.sh 2>&1 | tail -5; force=1; }
           lh=$R/gate_lifted_$frames.rph
           # regenerate when missing, asked, or older than the lifted binary
           if [ $force = 1 ] || [ ! -s $lh ] || [ $L -nt $lh ]; then
             echo "replay: lifted reference run ($frames frames)"; sh $T/replay_run.sh $lh $frames $L
           fi
           echo "replay: hybrid run"; sh $T/replay_run.sh $R/gate_hybrid.rph $frames
           python3 $T/replay_cmp.py $R/gate_hybrid.rph $lh --mask $T/replay/mask_stack.txt --fail > $R/gate_cmp.txt; rc=$?
           grep -v ' 0 pages differ$' $R/gate_cmp.txt | awk '/^frame/{n++; if(n>3) next} {print}'
           [ $rc = 0 ] && echo "REPLAY OK: $frames frames identical to all-lifted" || echo "REPLAY FAIL (full table: $R/gate_cmp.txt; triage: python3 $T/replay_triage.py)"
           exit $rc ;;
  replayall) # every replay gate: M01 12000, M02 2500, T01 T02 T04 T05 6000 (pads in tools/replay/*.pad, lifted refs in avpe-recomp/replay)
           R=/home/claude/avpe-recomp/replay; P=$T/replay; export AVPE_LOCKED=1; rc=0
           for t in T01 T02 T03 T04 T05; do l=$(echo $t | tr T t); n=6000; [ $t = T03 ] && n=9000
             [ -s $R/lifted_${t}_pad.rph ] || { echo "$t: lifted reference missing"; rc=1; continue; }
             MISSION=$t AVPE_REPLAY_PAD=$P/$l.pad sh $T/replay_run.sh $R/v_$t.rph $n >/dev/null
             python3 $T/replay_cmp.py $R/v_$t.rph $R/lifted_${t}_pad.rph --mask $P/mask_stack.txt --fail >/dev/null && echo "$t OK" || { echo "$t FAIL"; rc=1; }; done
           # free-running missions with m01.pad, pause menu with pause.pad: "name mission pad frames [extra mask]"
           for g in "PAUSE M01 pause 4200" "M03 M03 m01 3000" "M05 M05 m01 3000 mask_m05" "M06 M06 m01 3000" "M09 M09 m01 3000" "M10 M10 m01 2600" "M02P M02 m02 7300" "A01 A01 m01 6000" "P01 P01 m01 6000"; do
             set -- $g; n=$1; ms=$2; pd=$3; fr=$4; xm=""; [ -n "$5" ] && xm="--mask $P/$5.txt"
             [ -s $R/lifted_${n}_pad.rph ] || { echo "$n: lifted reference missing"; rc=1; continue; }
             MISSION=$ms AVPE_REPLAY_PAD=$P/$pd.pad sh $T/replay_run.sh $R/v_$n.rph $fr >/dev/null
             python3 $T/replay_cmp.py $R/v_$n.rph $R/lifted_${n}_pad.rph --mask $P/mask_stack.txt $xm --fail >/dev/null && echo "$n OK" || { echo "$n FAIL"; rc=1; }; done
           MISSION=M02 sh $T/replay_run.sh $R/v_M02.rph 2500 >/dev/null
           python3 $T/replay_cmp.py $R/v_M02.rph $R/lifted_M02.rph --mask $P/mask_stack.txt --fail >/dev/null && echo "M02 OK" || { echo "M02 FAIL"; rc=1; }
           sh $T/replay_run.sh $R/gate_hybrid.rph 12000 >/dev/null
           python3 $T/replay_cmp.py $R/gate_hybrid.rph $R/gate_lifted_12000.rph --mask $P/mask_stack.txt --fail >/dev/null && echo "M01 OK" || { echo "M01 FAIL"; rc=1; }
           [ $rc = 0 ] && echo "REPLAYALL OK" || echo "REPLAYALL FAIL"; exit $rc ;;
  *) echo "pipe.sh: rebuild | multi | mission | single | msingle | replay | replayall"; exit 2 ;;
esac
