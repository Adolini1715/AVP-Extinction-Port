#!/bin/sh
# build_lifted.sh: the all-lifted reference build for replay comparisons
# (notes/replay.md). Same lifter, same bridge and PC layer, but none of the
# decompiled C++ in avpe-decomp/src: every function is its lifted body.
# Separate tree (/home/claude/avpe-lifted), binary /home/claude/avpe-recomp/avpe_lifted
# (runs from the same game directory). Does not touch the shared build; runs
# niced. The lift is only redone when the bridge or PC layer's set of
# replaced functions changed; generated files recompile when they changed.
set -e
AVPE=/home/claude/avpe; XR=/home/claude/xboxrecomp; OUT=/home/claude/avpe-recomp; P=$OUT/project
L=/home/claude/avpe-lifted; DECOMP=/home/claude/avpe-decomp
mkdir -p $L/gen $L/obj $L/obj/sums $L/src
cp $AVPE/recomp/avpe_bridge.c $L/src/avpe_bridge.c
PLAT=$(ls $DECOMP/platform/*.cpp)
cat $L/src/avpe_bridge.c $PLAT > $L/src/manual_scan_all.c
# every original the PC layer calls by name (avpe_dec_VA) needs its lifted body kept as sub_VA_gen
for v in $(grep -oh 'avpe_dec_[0-9A-F]\{8\}' $PLAT | sort -u | cut -c10-); do
  echo "extern void sub_${v}_gen(void);"; done >> $L/src/manual_scan_all.c
EXTRA="--manual-functions $AVPE/recomp/manual_functions.json"
SIG=$( (grep -oh '^void sub_[0-9A-Fa-f]\{8\}(void)\|^extern void sub_[0-9A-Fa-f]\{8\}_gen(void);' $L/src/manual_scan_all.c | sort; \
        cat $AVPE/recomp/manual_functions.json $AVPE/recomp/false_starts.json; git -C $XR rev-parse HEAD) | sha1sum | cut -c1-40)
if [ "$(cat $L/obj/lift.sig 2>/dev/null)" != "$SIG" ]; then
  (cd $XR && nice -n 19 python3 -m tools.recomp $OUT/game_files/default.xbe --all --split 250 --gen-dir $L/gen \
      --exclude-manual $L/src/manual_scan_all.c $EXTRA > $L/recomp.log 2>&1) || { tail -20 $L/recomp.log; exit 1; }
  echo "$SIG" > $L/obj/lift.sig
fi
INC="-I $L/gen -I $P/src -I $P/src/recomp -I $XR/include $(find $XR/src -type d -printf '-I %p ') -I $AVPE/recomp"
for c in $L/gen/*.c; do
  b=$(basename $c .c); s=$(sha1sum $c | cut -c1-40)
  if [ ! -f $L/obj/$b.o ] || [ "$(cat $L/obj/sums/$b 2>/dev/null)" != "$s" ]; then
    nice -n 19 gcc -std=gnu11 -O1 -w -c $c $INC -o $L/obj/$b.o && echo $s > $L/obj/sums/$b
  fi
done
gcc -std=gnu11 -O1 -g -w -c $P/src/main_linux.c $INC -o $L/obj/main_linux.o
gcc -std=gnu11 -O1 -g -w -include stddef.h -c $P/src/recomp_manual.c $INC -o $L/obj/recomp_manual.o
gcc -std=gnu11 -O1 -g -w -c $L/src/avpe_bridge.c $INC -o $L/obj/avpe_bridge.o
# The PC layer calls decompiled originals by name (avpe_dec_VA): here, their lifted bodies.
for v in $(grep -oh 'avpe_dec_[0-9A-F]\{8\}' $PLAT | sort -u | cut -c10-); do
  printf 'extern void sub_%s_gen(void);\nvoid avpe_dec_%s(void){sub_%s_gen();}\n' $v $v $v
done > $L/src/dec_stubs.c
# PC features written in the decompiled sources (pc_select.cpp's box select and select all) have no
# lifted body: stubbed here, replays never drag a box or press Ctrl+A.
printf 'unsigned avpe_pc_box_select(unsigned p,float a,float b,float c,float d,int e){return 0;}\nunsigned avpe_pc_select_all(unsigned p){return 0;}\n' >> $L/src/dec_stubs.c
gcc -O1 -c $L/src/dec_stubs.c -o $L/obj/dec_stubs.o
for f in $PLAT; do
  nice -n 19 g++ -std=gnu++17 -O1 -g -w -fno-strict-aliasing -c $f -I $DECOMP/include -I $DECOMP/platform \
      -I $DECOMP/platform/glad/include $(pkg-config --cflags sdl2) $INC -o $L/obj/plat_$(basename $f .cpp).o
done
gcc -o $OUT/avpe_lifted $L/obj/*.o $OUT/obj/glad_gl.o -Wl,--start-group $(find /home/claude/xr-build -name '*.a') -Wl,--end-group \
    $(pkg-config --libs sdl2 gl libavcodec libavformat libavutil libswscale openssl) -lpthread -lm -ldl -lstdc++ -rdynamic
echo "linked $OUT/avpe_lifted"
