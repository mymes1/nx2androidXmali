"""Compile the real copied-pool deferral helpers without Android or a GPU."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('cc') and os.name != 'nt', 'POSIX C compiler required')
class CopiedPoolLazy(unittest.TestCase):
    def test_only_mmap_permission_errors_disable_later_zero_copy(self):
        core = (ROOT / 'nxnative/dm/android/gfx_vk_core.c').read_text()
        start = core.index('static _Atomic int g_zc_mmap_denied;')
        fn = core[start:core.index('\n}\n', start) + 3]
        code = '''#include <stdatomic.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>
#include <assert.h>
#define VKLOG(...) do { if (0) printf(__VA_ARGS__); } while(0)
''' + fn + '''
int main(void) {
 mem_diag_zc(1,"allocate",64,"errno",EACCES); assert(!g_zc_mmap_denied);
 mem_diag_zc(5,"mmap",64,"errno",ENOMEM); assert(!g_zc_mmap_denied);
 // Even when the stage's log was already emitted, remember its denial.
 mem_diag_zc(5,"mmap",64,"errno",EACCES); assert(g_zc_mmap_denied);
 g_zc_mmap_denied=0;
 mem_diag_zc(6,"mmap-fixed",64,"errno",EPERM); assert(g_zc_mmap_denied);
}
'''
        with tempfile.TemporaryDirectory(prefix='nx2a-copy-denial-') as tmp:
            c, exe = Path(tmp) / 'check.c', Path(tmp) / 'check'
            c.write_text(code)
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True, timeout=15)

    def test_first_use_policy_upload_publication_and_stale_pool(self):
        core = (ROOT / 'nxnative/dm/android/gfx_vk_core.c').read_text()
        start = core.index('static void pool_add_one(const void *mem, uint64_t size, uint64_t base, uint64_t end, int lazy) {')
        registration = core[start:core.index('\n}\n', start) + 3]
        code = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int VkResult;
#define VK_ERROR_FEATURE_NOT_PRESENT -1
#define VK_ERROR_DEVICE_LOST -4
#define VKCHK(r) do { if ((r)!=0) abort(); } while(0)
#define VKLOG(...) do {} while(0)
#define die(...) abort()
typedef struct {
 uint64_t lo,hi,serial,buf,mem,stamp,stamp0,base,end; int zero_copy;
 uint8_t *shadow,*baseline; const void *span;
 int lazy,synced,noncoherent,baseline_complete;
} vkpool_t;
static struct { struct { unsigned vendorID; } props; } V;
static vkpool_t g_pool[2]; static int g_npool=1, g_shadow_pools;
static uint64_t g_cp_bytes, g_lazy_total, g_zc_bytes;
static _Atomic int g_zc_mmap_denied; static unsigned g_pool_gen;
static pthread_rwlock_t g_pool_mu=PTHREAD_RWLOCK_INITIALIZER;
static pthread_mutex_t g_nest_mu=PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t g_lazy_mu=PTHREAD_MUTEX_INITIALIZER;
static int allocs,flushes,protected, cached_fail;
static int (*vkFlushMappedMemoryRanges)(void), (*vkInvalidateMappedMemoryRanges)(void);
static int dummy(void) { return 0; }
static VkResult pool_copied_alloc(vkpool_t *p,uint64_t size,int cached) {
 allocs++; if(cached && cached_fail) return -1;
 p->shadow=malloc(size); assert(p->shadow); memset(p->shadow,0xa5,size);
 p->buf=5; p->mem=6; p->noncoherent=cached; return 0;
}
static uint64_t nvn_dirty_now(void) { return 7; }
static const void *nvn_dirty_span(const void *p,uint64_t size) { (void)size; return p; }
static void nvn_dirty_protect(const void *p,uint64_t size) { (void)p;(void)size;protected++; }
static void pool_cache_sync(vkpool_t *p,int inv,uint64_t lo,uint64_t hi) {
 assert(!inv && lo==0 && hi==p->hi-p->lo); flushes++;
}
#define MAXPOOL 2
static uint64_t pool_ns(void) { return 1; }
static void pool_note(int k,uint64_t b,uint64_t t) { (void)k;(void)b;(void)t; }
static void pool_ord_insert(int n) { (void)n; }
static int zc_off(void) { return 0; }
static int zc_over(uint64_t n) { (void)n;return 0; }
static int hardware_calls;
static int pool_zero_copy(vkpool_t *p,uint8_t *m,uint64_t n,int l,const void *in) { (void)p;(void)m;(void)n;(void)l;(void)in;hardware_calls++;return -1; }
#include "gfx_vk_poolcopy.inc"
REGISTRATION
int main(int argc,char **argv) {
 V.props.vendorID=argc>1 && atoi(argv[1]) ? 0x13b5 : 0x5143;
 const char *e=getenv("DM_VK_COPY_LAZY");
 assert(copy_lazy_on()==(e ? atoi(e)!=0 : V.props.vendorID==0x13b5));
 uint8_t guest[64]; memset(guest,11,sizeof guest);
 if(copy_lazy_on()) {
   g_npool=0; g_zc_mmap_denied=1;
   pool_add_one(guest,64,(uintptr_t)guest,(uintptr_t)(guest+64),0);
   assert(g_npool==1 && g_pool[0].lazy==3 && !allocs && !hardware_calls && !g_cp_bytes);
   g_pool_gen=0;
 }
 g_pool[0]=(vkpool_t){.lo=(uintptr_t)guest,.hi=(uintptr_t)(guest+64),.serial=9,.lazy=3};
 // Merely registering the pending pool allocates no GPU storage or baseline.
 assert(!allocs && !g_pool[0].shadow && !g_pool[0].baseline);
 guest[0]=42;
 assert(copy_pool_back(&g_pool[0],9)==0);
 assert(allocs==1 && flushes==1 && protected==1 && !g_pool[0].lazy);
 assert(g_pool[0].synced && g_pool[0].stamp==7 && g_shadow_pools && g_pool_gen==1 && g_cp_bytes==64);
 assert(!memcmp(guest,g_pool[0].shadow,64) && !memcmp(guest,g_pool[0].baseline,64));
 assert(copy_pool_back(&g_pool[0],9)==0 && allocs==1); // no double allocation
 assert(copy_pool_back(&g_pool[0],10)==1 && allocs==1); // removed/replaced entry
 free(g_pool[0].shadow);free(g_pool[0].baseline);
 // Eager copied pools first bound AFTER begin_segment also need a seed.
 vkpool_t eager={.lo=(uintptr_t)guest,.hi=(uintptr_t)(guest+64)};
 vkFlushMappedMemoryRanges=vkInvalidateMappedMemoryRanges=dummy;
 setenv("DM_VK_POOL_CACHED","1",1);cached_fail=1;
 copy_pool_alloc(&eager,64);assert(allocs==3); // cached refused -> coherent
 copy_pool_seed(&eager);assert(eager.synced && flushes==2 && !memcmp(eager.shadow,guest,64));
 copy_pool_seed(&eager);assert(flushes==2); // never overwrite a live GPU copy
 free(eager.shadow);free(eager.baseline);
}
'''.replace('REGISTRATION', registration)
        with tempfile.TemporaryDirectory(prefix='nx2a-copy-lazy-') as tmp:
            c, exe = Path(tmp) / 'check.c', Path(tmp) / 'check'
            c.write_text(code)
            subprocess.run(['cc', '-std=gnu11', '-pthread', '-Wall', '-Wextra', '-Werror',
                '-I', str(ROOT / 'nxnative/dm/android'), str(c), '-o', str(exe)], check=True)
            for vendor in ('0', '1'):
                for setting in (None, '0', '1'):
                    env = dict(os.environ)
                    env.pop('DM_VK_POOL_CACHED', None)
                    env.pop('DM_VK_COPY_LAZY', None)
                    if setting is not None:
                        env['DM_VK_COPY_LAZY'] = setting
                    subprocess.run([str(exe), vendor], check=True, env=env, timeout=15)
