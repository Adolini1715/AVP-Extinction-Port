#!/bin/sh
# verify_padrun.sh <MISSION> <verify_multi args>: verify_multi in a mission driven by its
# deterministic replay pad (tools/replay/<mission>.pad, lower case), e.g. T04 opens the
# Dropship order menu, orders, upgrades: the order panel code runs. Missions without a pad use m01.pad
# (the A01/P01/M03-M10 gates do). Holds the pipeline lock.
M=$1; shift; m=$(echo $M | tr A-Z a-z)
[ -f /home/claude/avpe/tools/replay/$m.pad ] || m=m01    # missions without their own pad: the M01 one (as the gates)
cd /home/claude/avpe; exec 9>/tmp/avpe-pipeline.lock; flock 9
AVPE_REPLAY=fixed AVPE_REPLAY_PAD=/home/claude/avpe/tools/replay/$m.pad AVPE_LEVEL=$M/background.tbd AVPE_LEVEL_AT=3 timeout 1000 python3 tools/verify_multi.py "$@" 2>&1 | grep -v "^ *$" | tail -20
