#!/bin/sh
# Cross-build the PC build for Windows (x86-64) with MinGW-w64, from the
# same generated C as the Linux build (run rebuild.sh first: it lifts).
# Output: $OUTW/dist/avpe.exe with SDL2.dll; the game files go in a folder
# named game next to it (the disc's files: default.xbe, tbd\, *.wma, ...).
#
# Needs: mingw-w64 (x86_64-w64-mingw32-gcc/g++), the toolkit built for
# Windows in /home/claude/xr-build-win (cmake -DCMAKE_TOOLCHAIN_FILE=
# mingw.cmake), SDL2's MinGW development package in /home/claude/winlibs.
set -e
AVPE=/home/claude/avpe; XR=/home/claude/xboxrecomp; OUT=/home/claude/avpe-recomp; P=$OUT/project
DECOMP=/home/claude/avpe-decomp
OUTW=/home/claude/avpe-win; XRW=/home/claude/xr-build-win
SDL=/home/claude/winlibs/SDL2-2.30.9/x86_64-w64-mingw32
# FFmpeg for the movies: a minimal LGPL shared build (n7.1: the xmv demuxer, wmv2 and pcm
# decoders, swscale), configured as in FFMPEG-BUILD.txt next to the DLLs
FF=/home/claude/winlibs/ffmpeg-win
CC=x86_64-w64-mingw32-gcc; CXX=x86_64-w64-mingw32-g++
mkdir -p $OUTW/obj $OUTW/obj/sums $OUTW/dist
cp $AVPE/recomp/avpe_bridge.c $P/src/avpe_bridge.c
cd $P
INC="-I src -I src/recomp -I src/recomp/gen -I $XR/include $(find $XR/src -type d -printf '-I %p ') -I $AVPE/recomp"

# generated C, the bridge, the hand-written runtime parts: incremental by content
{
  for c in src/recomp/gen/*.c src/recomp_manual.c src/avpe_bridge.c $AVPE/recomp/main_windows.c; do
    echo "$c c"
  done
  for f in $DECOMP/src/*.cpp $DECOMP/platform/*.cpp; do
    grep -q '^void \(sub\|avpe_dec\)_[0-9A-Fa-f]\{8\}(void)' $f || case $f in $DECOMP/platform/*) ;; *) continue ;; esac
    echo "$f cpp"
  done
  echo "$DECOMP/platform/glad/src/gl.c glad"
} > $OUTW/obj/all.txt
HSUM=$(cat $DECOMP/include/avpe/*.h $DECOMP/platform/*.h | sha1sum | cut -c1-40)
# the build stamp in avpe.log's first lines: date, decomp commit, tools commit (+ if either has local changes)
dirty() { [ -n "$(git -C $1 status --porcelain -- src platform include recomp 2>/dev/null)" ] && echo + || true; }
BUILDID="$(date -u +%Y-%m-%d) decomp $(git -C $DECOMP rev-parse --short HEAD)$(dirty $DECOMP) tools $(git -C $AVPE rev-parse --short HEAD)$(dirty $AVPE)"
: > $OUTW/obj/todo.txt
while read f kind; do
  b=$(echo $f | sed 's|.*/||; s|\.[a-z]*$||'); [ $kind = cpp ] && b=decomp_$b
  s="$(sha1sum $f | cut -c1-40)$HSUM"; [ $b = main_windows ] && s="$s $BUILDID"
  [ -f $OUTW/obj/$b.o ] && [ "$(cat $OUTW/obj/sums/$b 2>/dev/null)" = "$s" ] || echo "$f $kind $b $s" >> $OUTW/obj/todo.txt
done < $OUTW/obj/all.txt
echo "compiling $(wc -l < $OUTW/obj/todo.txt) files for Windows"
# objects of sources that left the build
for o in $OUTW/obj/*.o; do
  [ -e "$o" ] || continue
  b=$(basename $o .o)
  grep -q " $b " $OUTW/obj/todo.txt && continue
  awk '{print $1}' $OUTW/obj/all.txt | sed 's|.*/||; s|\.[a-z]*$||' | grep -qx "${b#decomp_}" || rm -f $o $OUTW/obj/sums/$b
done
CXXFLAGS="-std=gnu++17 -O1 -w -fno-strict-aliasing -I $DECOMP/include -I $DECOMP/platform -I $DECOMP/platform/glad/include -I $SDL/include/SDL2 -I $FF/include"
while read f kind b s; do
  case $kind in
    c)    cmd="$CC -std=gnu11 -O1 -w -c $f $INC -o $OUTW/obj/$b.o"
          [ $b = main_windows ] && cmd="$CC -std=gnu11 -O1 -w '-DAVPE_BUILD_ID=\"$BUILDID\"' -c $f $INC -o $OUTW/obj/$b.o" ;;
    glad) cmd="$CC -O1 -c $f -I $DECOMP/platform/glad/include -o $OUTW/obj/$b.o" ;;
    cpp)  cmd="$CXX $CXXFLAGS -c $f $INC -o $OUTW/obj/$b.o" ;;
  esac
  echo "$cmd && echo $s > $OUTW/obj/sums/$b || echo FAILED $f"
done < $OUTW/obj/todo.txt > $OUTW/obj/cmds.txt
xargs -r -P2 -d '\n' -n1 sh -c < $OUTW/obj/cmds.txt

# the icon resource (kept out of obj/, whose stale objects are pruned above)
x86_64-w64-mingw32-windres -I $AVPE/recomp $AVPE/recomp/avpe.rc -O coff -o $OUTW/avpe_res.o
$CXX -o $OUTW/dist/avpe.exe $OUTW/obj/*.o $OUTW/avpe_res.o -Wl,--start-group $(find $XRW -name '*.a' -not -path '*/d3d8_smoke/*') -Wl,--end-group \
    -L$SDL/lib -lSDL2 -L$FF/lib -lavformat -lavcodec -lswscale -lavutil -lopengl32 -ld3dcompiler -ld3d11 -ldxgi -ldsound -lxinput -lmfplat -lmfreadwrite -lmfuuid -lmf -lws2_32 -lwinmm -lole32 -loleaut32 -luuid -lsetupapi -limm32 -lversion \
    -lbcrypt -ladvapi32 -luser32 -lgdi32 -lshell32 -lkernel32 \
    -static-libgcc -static-libstdc++ -Wl,-Bstatic -lwinpthread -Wl,-Bdynamic -mwindows
cp $SDL/bin/SDL2.dll $OUTW/dist/
cp $FF/bin/*.dll $OUTW/dist/
cp /home/claude/winlibs/ffmpeg-src/COPYING.LGPLv2.1 $OUTW/dist/FFMPEG-LICENSE.txt
{ echo "The movies are decoded by FFmpeg (https://ffmpeg.org), LGPL 2.1 or later, used as"
  echo "unmodified shared libraries (the avcodec, avformat, avutil and swscale DLLs here)."
  echo "Source: https://github.com/FFmpeg/FFmpeg tag n7.1. Built with:"
  sed -n 's/^# //p; /^\.\/configure/p' /home/claude/winlibs/ffmpeg-win/BUILD.txt 2>/dev/null; } > $OUTW/dist/FFMPEG-BUILD.txt
echo "built $OUTW/dist/avpe.exe"
