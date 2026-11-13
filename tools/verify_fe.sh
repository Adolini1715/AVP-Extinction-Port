#!/bin/sh
# verify_fe.sh <pad> <verify_multi args>: verify_multi in the front end (no level substitution),
# driven by a deterministic replay pad (absolute poll numbers, e.g. "400 start\n600 a"), with a
# fresh save directory (AVPE_SAVE_SEED=dir copies a starting disk in). Profile and save code:
# the CZFile, XAPI save-container and clock calls are externs (FE_EXTERN adds more).
# Pads: tools/replay/fe_new.pad (fresh disk), fe_load.pad and fe_del.pad (AVPE_SAVE_SEED=tools/replay/fe_seed1).
# Holds the pipeline lock.
P=$(readlink -f "$1"); shift
cd /home/claude/avpe; exec 9>/tmp/avpe-pipeline.lock; flock 9
SD=$(mktemp -d /tmp/avpe-fe-saves.XXXXXX)
[ -n "$AVPE_SAVE_SEED" ] && cp -a "$AVPE_SAVE_SEED/." "$SD/"
X=141090,14E0C0,14DE5D,143AE8,1329C0,132AC0,132A50,132C40,132C60,132C90,13ED70,13ECC0,13EBB0,1432B8,132780,1326F0,11FE00,120240,1207A0,12DF90,E1620,E1520
# a function being verified is not also an extern (its own calls would be replayed, not checked)
for a in "$@"; do v=$(echo "$a" | sed 's/:.*//' | tr a-f A-F | sed 's/^0*//'); X=$(echo ",$X," | sed "s/,0*$v,/,/g; s/^,//; s/,$//"); done
AVPE_REPLAY=fixed AVPE_REPLAY_FRAMES=${FE_FRAMES:-2000} AVPE_REPLAY_PAD=$P AVPE_NOLEVEL=1 AVPE_SAVE_DIR=$SD \
AVPE_REPLAY_EXCLUDE=740020-740024,2E01E2-2E01E6,2F69AC-2F69B0 \
VERIFY_EXTERN=$X${FE_EXTERN:+,$FE_EXTERN} timeout 1000 python3 tools/verify_multi.py "$@" 2>&1 | grep -v "^ *$" | tail -${FE_TAIL:-400}
case "$SD" in /tmp/avpe-fe-saves.*) rm -r -- "$SD" ;; esac
