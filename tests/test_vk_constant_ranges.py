"""Bounded constants must not back an entire deferred copied pool."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import csrc
ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('cc'), 'host C compiler required')
class ConstantRanges(unittest.TestCase):
    def test_production_descriptor_and_cpu_word(self):
        src = (ROOT/'nxnative/dm/android/gfx_vk_exec_descriptors.inc').read_text()
        functions = csrc.function(src, 'static void cbuf_desc(') + csrc.function(src, 'static uint32_t ubo_word(')
        code = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
typedef uint64_t VkBuffer;
typedef uint64_t VkDeviceSize;
typedef struct {uint64_t buffer,offset,range;} VkDescriptorBufferInfo;
typedef struct {int reg;} dmsv_bind;
typedef struct {int unused;} vkshader_t;
typedef struct {uint64_t addr,size;} p_bind_buf;
typedef struct {uint64_t buf,lo,hi,end,boff;} vkpool_t;
static struct {p_bind_buf ubo[6][16];} S;
static struct {struct {struct {uint64_t minUniformBufferOffsetAlignment;} limits;} props;} V;
static uint64_t g_set_epoch,g_ubd_gen;
static int g_var_now,pending,copied=1,backings,reads,allocs,valid=1;
static unsigned char guest[65536],scratch[65536];
static vkpool_t pool;
static int vk_pool_copied_range(uint64_t a,uint64_t n) {return copied && a==(uintptr_t)guest && n<=sizeof guest;}
static int vk_gpu_pending(uint64_t a,uint64_t n) {(void)a;(void)n;return pending;}
static void *vk_scratch(uint64_t n,uint64_t al,VkBuffer *b,VkDeviceSize *o) {assert(n<=sizeof scratch && al==256);allocs++;*b=7;*o=256;return scratch;}
static void vk_ubd_read(uint64_t a,uint64_t n,uint8_t *d) {reads++;memcpy(d,(void *)(uintptr_t)a,n);if(n>4)d[4]=99;}
static int mtl_addr(uint64_t a,uint64_t n,void **b,uint64_t *o) {(void)b;(void)o;return valid && a>=(uintptr_t)guest && a+n<=(uintptr_t)(guest+sizeof guest);}
static vkpool_t *vk_pool_of(uint64_t a,uint64_t n) {(void)a;(void)n;backings++;return valid ? &pool : NULL;}
static uint64_t vk_zero_buffer(void) {return 9;}
static void ubo_log(const vkshader_t *s,int st,int reg,uint64_t a,uint64_t n) {(void)s;(void)st;(void)reg;(void)a;(void)n;}
static void vk_cross(int k,uint64_t a,uint64_t n,const vkpool_t *p) {(void)k;(void)a;(void)n;(void)p;}
static int vk_ubd_shadow(uint64_t a,uint64_t n,VkBuffer *b,VkDeviceSize *o) {(void)a;(void)n;(void)b;(void)o;return 0;}
#define VKRED_ONCE(...) ((void)0)
FUNCTIONS
int main(void) {
 V.props.limits.minUniformBufferOffsetAlignment=256;
 pool=(vkpool_t){8,(uintptr_t)guest,(uintptr_t)(guest+sizeof guest),(uintptr_t)(guest+sizeof guest),0};
 S.ubo[0][0]=(p_bind_buf){(uintptr_t)guest,32};dmsv_bind b={0};vkshader_t s={0};VkDescriptorBufferInfo d;
 guest[0]=42;cbuf_desc(0,&s,&b,&d);
 assert(!backings && allocs==1 && reads==1 && d.buffer==7 && d.range==32 && scratch[0]==42 && scratch[4]==99);
 pending=1;cbuf_desc(0,&s,&b,&d);assert(backings==1 && allocs==1 && d.buffer==8);
 pending=0;copied=0;cbuf_desc(0,&s,&b,&d);assert(backings==2 && allocs==1 && d.buffer==8);
 copied=1;g_var_now=9;cbuf_desc(0,&s,&b,&d);assert(d.buffer==9 && allocs==1 && backings==2);g_var_now=0;
 S.ubo[0][0].size=100000;cbuf_desc(0,&s,&b,&d);assert(d.range==65536 && !memcmp(scratch,guest,4));
 cbuf_desc(-1,&s,&b,&d);assert(d.buffer==9);cbuf_desc(6,&s,&b,&d);assert(d.buffer==9);
 S.ubo[0][0].size=32;int before=backings;assert(ubo_word(0,0,0)==42 && backings==before);
 valid=0;assert(ubo_word(0,0,0)==0 && backings==before);
 assert(ubo_word(0,-1,0)==0 && ubo_word(0,0,32)==0);
 assert(ubo_word(-1,0,0)==0 && ubo_word(6,0,0)==0);
 S.ubo[0][0]=(p_bind_buf){UINT64_MAX-1,32};assert(ubo_word(0,0,0)==0);
}
'''.replace('FUNCTIONS', functions)
        with tempfile.TemporaryDirectory(prefix='nx2a-constant-ranges-') as tmp:
            c, exe = Path(tmp)/'check.c', Path(tmp)/'check'
            c.write_text(code)
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True, timeout=15)

    def test_real_eligibility_rejects_backed_and_cross_chunk_ranges(self):
        core = (ROOT/'nxnative/dm/android/gfx_vk_core.c').read_text()
        fn = csrc.function(core, 'int vk_pool_copied_range(')
        code = r'''
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
typedef struct {int zero_copy,lazy;void *shadow;uint64_t hi,end;} vkpool_t;
static vkpool_t p;static int present=1,finds;
static vkpool_t *pool_find(uint64_t a,uint64_t n,int gpu) {assert(a==4096 && n==1 && !gpu);finds++;return present ? &p : NULL;}
FUNCTION
int main(void) {
 p=(vkpool_t){.lazy=3,.hi=8192,.end=16384};
 assert(vk_pool_copied_range(4096,4096));
 assert(!vk_pool_copied_range(4096,4097));
 p.shadow=&p;assert(!vk_pool_copied_range(4096,4));p.shadow=NULL;
 p.lazy=0;assert(!vk_pool_copied_range(4096,4));p.lazy=1;assert(!vk_pool_copied_range(4096,4));p.lazy=3;
 p.zero_copy=1;assert(!vk_pool_copied_range(4096,4));p.zero_copy=0;
 present=0;assert(!vk_pool_copied_range(4096,4));
 int before=finds;assert(!vk_pool_copied_range(UINT64_MAX-1,4) && !vk_pool_copied_range(4096,0) && finds==before);
}
'''.replace('FUNCTION', fn)
        with tempfile.TemporaryDirectory(prefix='nx2a-constant-policy-') as tmp:
            c, exe = Path(tmp)/'check.c', Path(tmp)/'check'
            c.write_text(code)
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True, timeout=15)
