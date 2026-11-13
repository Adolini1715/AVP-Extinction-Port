#!/bin/sh
# Incremental rebuild of the native test build after changing overrides in
# project/src/avpe_bridge.c (or the symbol DB). Re-lifts with the bridge file
# excluded from generation, recompiles only generated files whose content
# changed, rebuilds the host and bridge, relinks.
set -e
AVPE=/home/claude/avpe; XR=/home/claude/xboxrecomp; OUT=/home/claude/avpe-recomp; P=$OUT/project
cp $AVPE/recomp/avpe_bridge.c $P/src/avpe_bridge.c 2>/dev/null || true
cp $AVPE/recomp/main_linux.c $P/src/main_linux.c
EXTRA=""
[ -f $AVPE/recomp/manual_functions.json ] && EXTRA="--manual-functions $AVPE/recomp/manual_functions.json" && EXTRA_FILE=$AVPE/recomp/manual_functions.json
# False function starts merged back into their owners (a split that loses
# flags across a fallthrough; recomp/false_starts.json, tools/false_starts.py).
python3 $AVPE/tools/false_starts.py $AVPE/recomp/false_starts.json > /dev/null
EXTRA_FILE="$EXTRA_FILE $AVPE/recomp/false_starts.json"
cd $XR
# Decompiled C++ (avpe-decomp/src): a file joins the build once it defines a
# replacement `void sub_XXXXXXXX(void)`, or an original the PC layer wraps
# (`void avpe_dec_XXXXXXXX(void)`). The lifter scans one file for those,
# so the bridge and the decompiled sources are concatenated for the scan.
DECOMP=/home/claude/avpe-decomp
DSRC=$(grep -l '^void \(sub\|avpe_dec\)_[0-9A-Fa-f]\{8\}(void)' $DECOMP/src/*.cpp $DECOMP/platform/*.cpp 2>/dev/null || true)
PLAT=$(ls $DECOMP/platform/*.cpp 2>/dev/null || true)
# (the AVPE-INDEX comment blocks list every function of a file; the scan
# would take those names as hand-written references and protect them)
cat $P/src/avpe_bridge.c $DSRC | sed '/AVPE-INDEX BEGIN/,/AVPE-INDEX END/d' > $P/src/manual_scan_all.c
# Re-lift only when the set of replaced functions changed (or the lifter's
# inputs did): the lift takes minutes and its output depends on nothing else.
LSIG=$( (grep -oh '^void sub_[0-9A-Fa-f]\{8\}(void)' $P/src/manual_scan_all.c | sort; \
         cat $EXTRA_FILE 2>/dev/null; git -C $XR rev-parse HEAD; git -C $XR diff --stat -- tools) | sha1sum | cut -c1-40)
if [ "$(cat $OUT/obj/lift.sig 2>/dev/null)" != "$LSIG" ] || [ -n "$AVPE_RELIFT" ]; then
python3 -m tools.recomp $OUT/game_files/default.xbe --all --split 250 --gen-dir $P/src/recomp/gen \
    --exclude-manual $P/src/manual_scan_all.c $EXTRA > $OUT/recomp.log 2>&1 || { tail -20 $OUT/recomp.log; exit 1; }
echo "$LSIG" > $OUT/obj/lift.sig
else
echo "replaced functions unchanged: no re-lift"
fi
cd $P
INC="-I src -I src/recomp -I src/recomp/gen -I $XR/include $(find $XR/src -type d -printf '-I %p ') -I $AVPE/recomp"
mkdir -p $OUT/obj $OUT/obj/sums
for c in src/recomp/gen/*.c; do
  b=$(basename $c .c); s=$(sha1sum $c | cut -c1-40)
  if [ ! -f $OUT/obj/$b.o ] || [ "$(cat $OUT/obj/sums/$b 2>/dev/null)" != "$s" ]; then echo "$c $s"; fi
done > $OUT/obj/todo.txt
# objects of generated files that no longer exist (the lift made fewer files)
for o in $OUT/obj/recomp_[0-9]*.o; do
  [ -e "$o" ] || continue
  [ -f src/recomp/gen/$(basename $o .o).c ] || rm -f $o $OUT/obj/sums/$(basename $o .o)
done
echo "recompiling $(wc -l < $OUT/obj/todo.txt) generated files"
cat $OUT/obj/todo.txt | xargs -r -P2 -L1 sh -c "gcc -std=gnu11 -O1 -w -c \$0 $INC -o $OUT/obj/\$(basename \$0 .c).o && echo \$1 > $OUT/obj/sums/\$(basename \$0 .c)"
gcc -std=gnu11 -O1 -g -w -c src/main_linux.c $INC -o $OUT/obj/main_linux.o
gcc -std=gnu11 -O1 -g -w -include stddef.h -c src/recomp_manual.c $INC -o $OUT/obj/recomp_manual.o
gcc -std=gnu11 -O1 -g -w -c src/avpe_bridge.c $INC -o $OUT/obj/avpe_bridge.o
# Decompiled C++: incremental (a file is recompiled when it or any shared
# header changed), two at a time; objects of files that left the build go.
mkdir -p $OUT/obj/dsums
HSUM=$(cat $DECOMP/include/avpe/*.h | sha1sum | cut -c1-40)
for o in $OUT/obj/decomp_*.o; do
  [ -e "$o" ] || continue
  b=$(basename $o .o); b=${b#decomp_}
  echo "$DSRC $PLAT" | grep -q "/$b.cpp" || rm -f $o $OUT/obj/dsums/$b
done
for f in $(echo $DSRC $PLAT | tr " " "\n" | sort -u); do
  b=$(basename $f .cpp); s="$(sha1sum $f | cut -c1-40)$HSUM"
  if [ ! -f $OUT/obj/decomp_$b.o ] || [ "$(cat $OUT/obj/dsums/$b 2>/dev/null)" != "$s" ]; then echo "$f $s"; fi
done > $OUT/obj/dtodo.txt
echo "compiling $(wc -l < $OUT/obj/dtodo.txt) decompiled source files"
cat $OUT/obj/dtodo.txt | xargs -r -P2 -L1 sh -c "g++ -std=gnu++17 -O1 -g -w -fno-strict-aliasing -c \$0 -I $DECOMP/include -I $DECOMP/platform -I $DECOMP/platform/glad/include $(pkg-config --cflags sdl2) $INC -o $OUT/obj/decomp_\$(basename \$0 .cpp).o && echo \$1 > $OUT/obj/dsums/\$(basename \$0 .cpp) || echo FAILED \$0"
echo "decompiled source files in build: $(echo $DSRC | wc -w)"
# the OpenGL loader (glad, generated): C
[ $OUT/obj/glad_gl.o -nt $DECOMP/platform/glad/src/gl.c ] || gcc -O1 -c $DECOMP/platform/glad/src/gl.c -I $DECOMP/platform/glad/include -o $OUT/obj/glad_gl.o
gcc -o $OUT/avpe_recomp $OUT/obj/*.o -Wl,--start-group $(find /home/claude/xr-build -name '*.a') -Wl,--end-group \
    $(pkg-config --libs sdl2 gl libavcodec libavformat libavutil libswscale openssl) -lpthread -lm -ldl -lstdc++ -rdynamic
echo "linked $OUT/avpe_recomp"
