#!/bin/sh
# build_toggle.sh: the per-function toggle build. Every decompiled sub_X in
# avpe-decomp/src keeps its lifted body (sub_X_gen, the lifter's wrapper
# routing) and the symbol sub_X becomes a dispatcher choosing, per call, the
# decompiled body (dec_X) or the lifted one. Used to bisect a replay divergence
# to one function without rebuilding (notes/replay.md).
# Binary: /home/claude/avpe-recomp/avpe_toggle. Selection at run time, env AVPE_DEC_SET:
#   all (default) | none | idx:LO:HI (sorted VA list, indices LO..HI-1 enabled)
#   on:FILE | off:FILE (FILE: one hex VA per line).  AVPE_DEC_LIST=FILE dumps the list.
# Reuses the compiled objects of the normal build (symbols renamed with objcopy);
# only the lift and the generated C files are separate (/home/claude/avpe-toggle).
set -e
AVPE=/home/claude/avpe; XR=/home/claude/xboxrecomp; OUT=/home/claude/avpe-recomp; P=$OUT/project
T=/home/claude/avpe-toggle; DECOMP=/home/claude/avpe-decomp
mkdir -p $T/gen $T/obj/sums $T/src $T/dobj
cp $AVPE/recomp/avpe_bridge.c $T/src/avpe_bridge.c
PLAT=$(ls $DECOMP/platform/*.cpp)
# VAs owned by the bridge or the platform layer stay as they are
(cat $T/src/avpe_bridge.c $PLAT | grep -oh '^void sub_[0-9A-Fa-f]\{8\}(void)\|^extern void sub_[0-9A-Fa-f]\{8\}_gen(void);' | grep -o '[0-9A-Fa-f]\{8\}' | tr a-f A-F | sort -u) > $T/owned.txt
(for f in $DECOMP/src/*.cpp; do sed '/AVPE-INDEX BEGIN/,/AVPE-INDEX END/d' $f | grep -o '^void sub_[0-9A-Fa-f]\{8\}(void)' | grep -o '[0-9A-Fa-f]\{8\}' | tr a-f A-F; done | sort -u | comm -23 - $T/owned.txt) > $T/list.txt
n=$(wc -l < $T/list.txt); echo "toggled functions: $n"
# dispatcher
{
cat <<'HDR'
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "recomp_types.h"
static unsigned char dec_on[0x400000];
static unsigned short abi_bad[2][0x400000];
static int abi_chk; static unsigned abi_n; static void abi_report(void);
#define CALLCHK(v,fn,w) do{ if(abi_chk){ if((++abi_n&0x1FFFF)==0) abi_report(); uint32_t _b=g_ebx,_s=g_esi,_d=g_edi,_p=g_esp; fn(); if(g_ebx!=_b||g_esi!=_s||g_edi!=_d||g_esp<_p+4){ unsigned short *c=&abi_bad[w][(v)>>2]; if(*c<65535)(*c)++; } } else fn(); }while(0)
static const uint32_t vas[] = {
HDR
sed 's/^/0x/;s/$/,/' $T/list.txt
cat <<'FTR'
};
#define N (sizeof(vas)/sizeof(vas[0]))
static int idx_of(uint32_t va){ int lo=0,hi=(int)N-1; while(lo<=hi){int m=(lo+hi)/2; if(vas[m]==va) return m; if(vas[m]<va) lo=m+1; else hi=m-1;} return -1; }
static void load_set(void){
  const char *s=getenv("AVPE_DEC_SET"), *l=getenv("AVPE_DEC_LIST");
  if(l){FILE*f=fopen(l,"w"); if(f){for(unsigned i=0;i<N;i++) fprintf(f,"%08X\n",vas[i]); fclose(f);}}
  int mode=0; unsigned lo=0,hi=0; FILE*f=0; char line[64];
  if(!s||!strcmp(s,"all")) mode=1; else if(!strcmp(s,"none")) mode=0;
  else if(!strncmp(s,"idx:",4)){ mode=2; sscanf(s+4,"%u:%u",&lo,&hi);}
  else if(!strncmp(s,"on:",3)){ mode=3; f=fopen(s+3,"r"); }
  else if(!strncmp(s,"off:",4)){ mode=4; f=fopen(s+4,"r"); }
  for(unsigned i=0;i<N;i++){ int on = mode==1 || mode==4 || (mode==2 && i>=lo && i<hi); dec_on[vas[i]>>2]=on; }
  if(f){ while(fgets(line,sizeof line,f)){ uint32_t v=strtoul(line,0,16); int i=idx_of(v); if(i>=0) dec_on[vas[i]>>2]=(mode==3); } fclose(f); }
  unsigned c=0; for(unsigned i=0;i<N;i++) c+=dec_on[vas[i]>>2];
  fprintf(stderr,"[TOGGLE] %u of %u decompiled functions enabled (set=%s)\n",c,(unsigned)N,s?s:"all");
}
static void abi_report(void){ FILE*f=fopen(getenv("AVPE_DEC_ABI"),"w"); if(!f) return; for(unsigned i=0;i<N;i++){ unsigned a=abi_bad[1][vas[i]>>2], b=abi_bad[0][vas[i]>>2]; if(a||b) fprintf(f,"%08X dec_violations=%u lifted_violations=%u\n",vas[i],a,b);} fclose(f); }
__attribute__((constructor)) static void toggle_init(void){ load_set(); if(getenv("AVPE_DEC_ABI")){ abi_chk=1; atexit(abi_report);} }
FTR
while read v; do
  echo "extern void sub_${v}_gen(void); extern void dec_${v}(void);"
  echo "void sub_${v}(void){ if(dec_on[0x${v}>>2]) CALLCHK(0x${v},dec_${v},1); else CALLCHK(0x${v},sub_${v}_gen,0); }"
done < $T/list.txt
} > $T/src/dispatch.c
cat $T/src/avpe_bridge.c $PLAT $T/src/dispatch.c > $T/src/manual_scan_all.c
EXTRA="--manual-functions $AVPE/recomp/manual_functions.json"
SIG=$( (grep -oh '^void sub_[0-9A-Fa-f]\{8\}(void)\|^extern void sub_[0-9A-Fa-f]\{8\}_gen(void);' $T/src/manual_scan_all.c | sort; \
        cat $AVPE/recomp/manual_functions.json $AVPE/recomp/false_starts.json; git -C $XR rev-parse HEAD) | sha1sum | cut -c1-40)
if [ "$(cat $T/obj/lift.sig 2>/dev/null)" != "$SIG" ]; then
  (cd $XR && nice -n 19 python3 -m tools.recomp $OUT/game_files/default.xbe --all --split 250 --gen-dir $T/gen \
      --exclude-manual $T/src/manual_scan_all.c $EXTRA > $T/recomp.log 2>&1) || { tail -20 $T/recomp.log; exit 1; }
  rm -f $T/obj/recomp_*.o; echo "$SIG" > $T/obj/lift.sig
fi
INC="-I $T/gen -I $P/src -I $P/src/recomp -I $XR/include $(find $XR/src -type d -printf '-I %p ')"
for c in $T/gen/*.c; do
  b=$(basename $c .c); s=$(sha1sum $c | cut -c1-40)
  if [ ! -f $T/obj/$b.o ] || [ "$(cat $T/obj/sums/$b 2>/dev/null)" != "$s" ]; then echo "$c $s"; fi
done > $T/todo.txt
echo "compiling $(wc -l < $T/todo.txt) generated files"
cat $T/todo.txt | xargs -r -P2 -L1 sh -c "nice -n 19 gcc -std=gnu11 -O1 -w -c \$0 $INC -o $T/obj/\$(basename \$0 .c).o && echo \$1 > $T/obj/sums/\$(basename \$0 .c)"
gcc -std=gnu11 -O1 -g -w -c $P/src/main_linux.c $INC -o $T/obj/main_linux.o
gcc -std=gnu11 -O1 -g -w -include stddef.h -c $P/src/recomp_manual.c $INC -o $T/obj/recomp_manual.o
gcc -std=gnu11 -O1 -g -w -c $T/src/avpe_bridge.c $INC -o $T/obj/avpe_bridge.o
gcc -std=gnu11 -O1 -g -w -c $T/src/dispatch.c $INC -o $T/obj/dispatch.o
# decompiled objects: src files get sub_X -> dec_X for the toggled VAs
sed 's/.*/sub_&  dec_&/' $T/list.txt > $T/redef.txt
rm -f $T/dobj/*.o
for o in $OUT/obj/decomp_*.o; do
  b=$(basename $o .o); src=$DECOMP/src/${b#decomp_}.cpp
  if [ -f $src ]; then objcopy --redefine-syms=$T/redef.txt $o $T/dobj/$b.o; else cp $o $T/dobj/$b.o; fi
done
gcc -o $OUT/avpe_toggle $T/obj/*.o $T/dobj/*.o $OUT/obj/glad_gl.o -Wl,--start-group $(find /home/claude/xr-build -name '*.a') -Wl,--end-group \
    $(pkg-config --libs sdl2 gl libavcodec libavformat libavutil libswscale openssl) -lpthread -lm -ldl -lstdc++ -rdynamic
echo "linked $OUT/avpe_toggle"
