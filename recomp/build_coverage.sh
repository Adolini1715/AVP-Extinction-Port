#!/bin/sh
# Coverage build: same as rebuild.sh (run that first), but the translated code
# is compiled with -finstrument-functions into $COV and linked with
# recomp/coverage_rt.c as avpe_recomp_cov. tools/coverage.py reads the dumps.
# Incremental rebuild of the native test build after changing overrides in
# project/src/avpe_bridge.c (or the symbol DB). Re-lifts with the bridge file
# excluded from generation, recompiles only generated files whose content
# changed, rebuilds the host and bridge, relinks.
set -e
AVPE=/home/claude/avpe; XR=/home/claude/xboxrecomp; OUT=/home/claude/avpe-recomp; P=$OUT/project; COV=$OUT/cov
cp $AVPE/recomp/avpe_bridge.c $P/src/avpe_bridge.c 2>/dev/null || true
cp $AVPE/recomp/main_linux.c $P/src/main_linux.c
EXTRA=""
[ -f $AVPE/recomp/manual_functions.json ] && EXTRA="--manual-functions $AVPE/recomp/manual_functions.json"
cd $XR
# Decompiled C++ (avpe-decomp/src): a file joins the build once it defines a
# replacement `void sub_XXXXXXXX(void)`. The lifter scans one file for those,
# so the bridge and the decompiled sources are concatenated for the scan.
DECOMP=/home/claude/avpe-decomp
DSRC=$(grep -l '^void sub_[0-9A-Fa-f]\{8\}(void)' $DECOMP/src/*.cpp 2>/dev/null || true)
cat $P/src/avpe_bridge.c $DSRC > $P/src/manual_scan_all.c
# (lifter output reused from rebuild.sh)
cd $P
INC="-I src -I src/recomp -I src/recomp/gen -I $XR/include $(find $XR/src -type d -printf '-I %p ')"
mkdir -p $COV/obj $COV/obj/sums
for c in src/recomp/gen/*.c; do
  b=$(basename $c .c); s=$(sha1sum $c | cut -c1-40)
  if [ ! -f $COV/obj/$b.o ] || [ "$(cat $COV/obj/sums/$b 2>/dev/null)" != "$s" ]; then echo "$c $s"; fi
done > $COV/obj/todo.txt
# objects of generated files that no longer exist
for o in $COV/obj/recomp_*.o; do
  [ -e "$o" ] || continue
  [ -f src/recomp/gen/$(basename $o .o).c ] || rm -f $o $COV/obj/sums/$(basename $o .o)
done
echo "recompiling $(wc -l < $COV/obj/todo.txt) generated files"
cat $COV/obj/todo.txt | xargs -r -P$(nproc) -L1 sh -c "gcc -std=gnu11 -O1 -w -finstrument-functions -c \$0 $INC -o $COV/obj/\$(basename \$0 .c).o && echo \$1 > $COV/obj/sums/\$(basename \$0 .c)"
# everything else (host, bridge, decompiled C++, platform layer, glad) is
# rebuild.sh's objects as they are: run rebuild.sh first
gcc -std=gnu11 -O1 -c $AVPE/recomp/coverage_rt.c -o $COV/coverage_rt.o
gcc -o $OUT/avpe_recomp_cov $COV/obj/recomp_*.o $(ls $OUT/obj/*.o | grep -v "/recomp_[0-9]\|/recomp_dispatch\|/recomp_stubs") $COV/coverage_rt.o -Wl,--start-group $(find /home/claude/xr-build -name '*.a') -Wl,--end-group \
    $(pkg-config --libs sdl2 gl libavcodec libavformat libavutil libswscale openssl) -lpthread -lm -ldl -lstdc++ -rdynamic
echo "linked $OUT/avpe_recomp_cov"
