"""Conservative eligibility and transfer-range coverage for bounded readback."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import csrc  # noqa: E402


class ReadbackRanges(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'a host C compiler is required')
    def test_eligibility_and_range_capacity(self):
        dm = Path(__file__).resolve().parents[1] / 'nxnative/dm/android'
        core = (dm/'gfx_vk_core.c').read_text(encoding='utf-8')
        exec_src = csrc.unit((dm/'gfx_vk_exec.c'))
        header = (dm/'gfx_vk.h').read_text(encoding='utf-8')
        enums = '\n'.join(line for line in header.splitlines() if line.startswith(('enum { RK_SUPPORT', 'enum { RD_UBO')))
        controls = '\n'.join(line for line in core.splitlines() if line.startswith(('int vk_readback_ranges(', 'void vk_readback_unknown(')))
        start = core.index('static void sub_gpu_wrote(uint64_t a, uint64_t n) {')
        ranges = csrc.function(core, 'static void sub_gpu_wrote(uint64_t a, uint64_t n) {')
        start = exec_src.index('static VkDescriptorSet stage_set(')
        gate = exec_src[exec_src.index('\n', start):exec_src.index('    int memo =', start)]
        # The gate must execute before descriptor memo/cache returns.
        self.assertIn('vk_readback_ranges()', gate)
        for marker in ['vk_gpu_wrote((uint64_t)(uintptr_t)t->mem, len);   // exact stride-aware extent',
                       'if (p) vk_gpu_wrote(c->src, p->hi - c->src); else vk_readback_unknown();',
                       'vk_readback_unknown();   // capture extents']:
            self.assertIn(marker, exec_src)
        harness = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
ENUMS
enum { GW_SUB=64 };
typedef struct { struct { uint64_t lo,hi; } gw[64]; int ngw,readback_ranges; } vksub_t;
static vksub_t g_slot[2]; static int g_cur=-1;
typedef struct { int kind,desc; } binding;
typedef struct { int nbind; binding b[2]; } vkshader_t;
CONTROLS
RANGES
static void observe(vkshader_t *s) {
GATE
}
int main(void) {
    assert(!vk_readback_ranges()); vk_readback_unknown();
    g_cur=0; g_slot[0].readback_ranges=1;
    int readonly[]={RD_UBO,RD_SAMPLED,RD_TBUF,RD_IMAGE,RD_SAMPLER};
    for (unsigned i=0;i<sizeof readonly/sizeof *readonly;i++) {
        vkshader_t s={.nbind=1,.b={{RK_CBUF,readonly[i]}}}; observe(&s); assert(vk_readback_ranges());
    }
    int writes[]={RD_STORAGE,RD_STORAGE_TBUF,255};
    for (unsigned i=0;i<sizeof writes/sizeof *writes;i++) {
        g_slot[0].readback_ranges=1; vkshader_t s={.nbind=1,.b={{RK_UBO_PTR,writes[i]}}};
        observe(&s); assert(!vk_readback_ranges()); observe(&s); assert(!vk_readback_ranges());
    }
    int bounded[]={RK_SBUF,RK_SBUF_UBO,RK_UBO_PTR};
    for (unsigned i=0;i<sizeof bounded/sizeof *bounded;i++) {
        g_slot[0].readback_ranges=1; vkshader_t s={.nbind=1,.b={{bounded[i],RD_SSBO}}};
        observe(&s); assert(vk_readback_ranges());
    }
    int kinds[]={RK_IMG,RK_IMG_UBO,RK_IMG_TBUF};
    for (unsigned i=0;i<sizeof kinds/sizeof *kinds;i++) {
        g_slot[0].readback_ranges=1; vkshader_t s={.nbind=1,.b={{kinds[i],RD_UBO}}};
        observe(&s); assert(!vk_readback_ranges());
    }
    memset(g_slot,0,sizeof g_slot); g_slot[0].readback_ranges=1;
    sub_gpu_wrote(0,0); assert(!g_slot[0].ngw);
    for (uint64_t i=0;i<100;i++) sub_gpu_wrote(1000+i*1000000,37);
    assert(g_slot[0].ngw<=GW_SUB);
    for (uint64_t i=0;i<100;i++) {
        uint64_t a=1000+i*1000000; int found=0;
        for (int j=0;j<g_slot[0].ngw;j++) found|=g_slot[0].gw[j].lo<=a && g_slot[0].gw[j].hi>=a+37;
        assert(found);
    }
    vk_readback_unknown(); assert(!vk_readback_ranges());
}
'''.replace('ENUMS', enums).replace('CONTROLS', controls).replace('RANGES', ranges).replace('GATE', gate)
        with tempfile.TemporaryDirectory(prefix='nx2a-readback-ranges-') as tmp:
            c, exe = Path(tmp)/'check.c', Path(tmp)/'check'
            c.write_text(harness, encoding='utf-8')
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True, timeout=15)

    @unittest.skipUnless(shutil.which('cc'), 'a host C compiler is required')
    def test_real_storage_descriptor_marks_all_stages_and_aligned_extent(self):
        dm = Path(__file__).resolve().parents[1] / 'nxnative/dm/android'
        src = (dm/'gfx_vk_exec_descriptors.inc').read_text()
        block = src[src.index('        case RK_SBUF: case RK_UBO_PTR:'):src.index('        case RK_TEX: case RK_TEX_UBO:')]
        enums = '\n'.join(line for line in (dm/'gfx_vk.h').read_text().splitlines()
                          if line.startswith(('enum { RK_SUPPORT', 'enum { RD_UBO')))
        harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
ENUMS
#define SB_ALIGN 256
#define VKRED_ONCE(...) ((void)0)
typedef uint64_t VkBuffer;
typedef uint64_t VkDeviceSize;
typedef struct { VkBuffer buffer; uint64_t offset,range; } VkDescriptorBufferInfo;
typedef struct { int kind,reg,off,desc; } binding;
typedef struct { uint64_t lo,hi,end,buf,boff; } vkpool_t;
static vkpool_t pool={4096,8192,8192,7,64};
static struct { struct { uint64_t addr,size; } ssbo[6][16],ubo[6][16]; } S;
static struct { struct { struct { unsigned minStorageBufferOffsetAlignment; } limits; } props; } V;
static uint64_t noted,extent; static int calls;
static vkpool_t *vk_pool_of(uint64_t a,uint64_t n) { (void)n; return a>=pool.lo && a<pool.hi ? &pool : NULL; }
static uint32_t ubo_word(int st,int reg,int off) { (void)st;(void)reg;return off==0 ? 4103 : 0; }
static void vk_cross(int k,uint64_t a,uint64_t n,vkpool_t *p) { (void)k;(void)a;(void)n;(void)p;assert(0); }
static uint64_t vk_zero_buffer(void) { return 99; }
static void vk_readback_wrote(uint64_t a,uint64_t n) { noted=a;extent=n;calls++; }
static void vk_gpu_wrote(uint64_t a,uint64_t n) { (void)a;(void)n; }
static VkDescriptorBufferInfo bind(int st,binding *b) {
 VkBuffer buf; VkDeviceSize off; VkDescriptorBufferInfo bi[1]; int nw=0;
 switch(b->kind) {
BLOCK
 default: assert(0);
 }
 return bi[0];
}
int main(void) {
 V.props.limits.minStorageBufferOffsetAlignment=256;
 for(int st=0;st<6;st++) {
   S.ssbo[st][0].addr=4103;S.ssbo[st][0].size=33;
   S.ubo[st][0].addr=4103;S.ubo[st][0].size=33;
   int kinds[]={RK_SBUF,RK_SBUF_UBO,RK_UBO_PTR};
   for(int k=0;k<3;k++) {
     binding b={kinds[k],0,0,RD_SSBO}; calls=0;
     VkDescriptorBufferInfo d=bind(st,&b);
     assert(calls==1 && noted==4096 && extent==(k==1 ? 4096 : 40));
     assert(d.buffer==7 && d.offset==64 && d.range==extent);
   }
   binding b={RK_UBO_PTR,0,0,RD_UBO};calls=0;bind(st,&b);assert(!calls);
   b=(binding){RK_SBUF,0,0,RD_SSBO};S.ssbo[st][0].addr=0;
   calls=0;assert(bind(st,&b).buffer==99 && !calls);
   S.ssbo[st][0].addr=8185;S.ssbo[st][0].size=100;calls=0;
   VkDescriptorBufferInfo d=bind(st,&b);
   assert(calls==1 && noted==7936 && extent==256 && d.range==256);
 }
}
'''.replace('ENUMS', enums).replace('BLOCK', block)
        with tempfile.TemporaryDirectory(prefix='nx2a-storage-ranges-') as tmp:
            c, exe = Path(tmp)/'check.c', Path(tmp)/'check'
            c.write_text(harness)
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True, timeout=15)
