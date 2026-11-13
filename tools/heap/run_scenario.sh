#!/bin/sh
# run_sc.sh <scenario> [extra env]: generate, run with --keep, dump heap, coverage, clean
S=$(dirname $0)
cd /home/claude/avpe
python3 $S/heapgen.py $1 /tmp/heap_sc.probes
VERIFY_EXTERN=${VERIFY_EXTERN:-143F29} sh tools/pipe.sh mission --probes /tmp/heap_sc.probes --frame 1150 --timeout 400 --keep 2>&1 | grep -E "DIFF|FAIL|    |checked" | cut -c1-220 | head -20
python3 $S/heapdump.py
find /home/claude/avpe-recomp/verify -name "*.bin*" -delete
