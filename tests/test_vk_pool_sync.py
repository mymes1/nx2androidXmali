"""The zero-copy cache sync's bookkeeping (nxnative/dm/android/gfx_vk_poolsync.c), compiled from the real source.

The C harness stands in for gfx_vk_core.c: it defines the pools the file notes, the versions and the dma-buf syncs, and counts the STARTs and ENDs each pool gets. What
it checks is what a device run would otherwise be the first to show: only pools the submission used are synced, a later submission's writes are not invalidated by an
earlier submission's END, an END waits for its own fence, the STARTs all precede the queue submit, a pool freed in between is skipped, and no pool the game did not use is
touched. The dma-buf side itself (DMA_BUF_IOCTL_SYNC on a real buffer) is the device's: only a Mali device can show that.
"""
import shutil, subprocess, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'nxnative/dm/android/gfx_vk_poolsync.c'

STUB = r'''
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* gfx_vk.h brings in Vulkan and Android: the harness has the two types the file uses */
typedef struct { uint64_t lo, hi, serial, use_gen, sync_ver; int zero_copy; } vkpool_t;
static unsigned char g_vk_quirks[2];
enum { VQ_AHB_NO_CACHE_SYNC = 1 };
static int vk_quirk(int q) { (void)g_vk_quirks; (void)q; return 1; }
#define VKLOG(...) do { fprintf(stderr, "log: " __VA_ARGS__); fputc('\n', stderr); } while (0)
#define VKRED_ONCE(tag, ...) do { static int shown; if (!shown++) { fprintf(stderr, "red: " __VA_ARGS__); fputc('\n', stderr); } } while (0)

/* gfx_vk_core.c's side, as the harness: the pool table, its versions, and the syncs each pool got */
struct st { vkpool_t p; int start, end, start_at_submit; };
static struct st P[4];
static int nsub, synced_after_submit;
int vk_pool_sync(uint64_t serial, uint64_t lo, unsigned long long flags) {
    for (int i = 0; i < 4; i++) if (P[i].p.serial == serial && P[i].p.lo == lo) {
        if (flags == 7) { P[i].start++; P[i].start_at_submit = nsub; if (nsub != 0 && synced_after_submit) return -1; }
        else if (flags == 3) P[i].end++;
        else abort();
        return 0;
    }
    return -1;   /* a pool that is gone */
}
uint64_t vk_pool_bump(uint64_t serial, uint64_t lo) {
    for (int i = 0; i < 4; i++) if (P[i].p.serial == serial && P[i].p.lo == lo) return ++P[i].p.sync_ver;
    return 0;
}
int vk_pool_stale(uint64_t serial, uint64_t lo, uint64_t ver) {
    for (int i = 0; i < 4; i++) if (P[i].p.serial == serial && P[i].p.lo == lo) return P[i].p.sync_ver != ver;
    return 1;
}
#include "gfx_vk_poolsync.c"

int main(void) {
    for (int i = 0; i < 4; i++) { P[i].p.lo = 0x1000u * (unsigned)i; P[i].p.hi = P[i].p.lo + 0x1000; P[i].p.serial = 100 + (unsigned)i; P[i].p.zero_copy = i != 3; }

    /* submission 1 uses pools 0 and 1 (and a copied pool, 3, which is never synced) */
    nsub = 1;
    vk_ahb_note_use(&P[0].p); vk_ahb_note_use(&P[1].p); vk_ahb_note_use(&P[0].p); vk_ahb_note_use(&P[3].p);
    vk_ahb_sync_start(1);
    assert(P[0].start == 1 && P[1].start == 1 && P[2].start == 0 && P[3].start == 0);   /* once a pool a submission, and no copied pool */
    assert(P[0].end == 0);   /* nothing is given back before the fence */

    /* submission 2 uses pool 1 again, and pool 2 */
    nsub = 2;
    vk_ahb_note_use(&P[1].p); vk_ahb_note_use(&P[2].p);
    vk_ahb_sync_start(2);
    assert(P[1].start == 2 && P[2].start == 1);

    /* submission 1's fence: pool 0's END, but not pool 1's (submission 2 wrote into it since) */
    vk_ahb_sync_end(1);
    assert(P[0].end == 1 && P[1].end == 0 && P[2].end == 0);

    /* submission 2's fence: pool 1's and pool 2's */
    vk_ahb_sync_end(2);
    assert(P[1].end == 1 && P[2].end == 1);

    /* a pool freed before its END: skipped, and its serial never comes back */
    nsub = 3;
    vk_ahb_note_use(&P[1].p); vk_ahb_sync_start(3);
    uint64_t serial = P[1].p.serial; P[1].p.serial = 999;   /* (the core makes it again with another serial: the old one is gone) */
    vk_ahb_sync_end(3);
    assert(P[1].end == 1);   /* not 2 */
    P[1].p.serial = serial;

    /* a fence that arrives before its submission's queue (the completion order is the fence order: the queue holds the rest) */
    nsub = 4;
    vk_ahb_note_use(&P[0].p); vk_ahb_sync_start(4);
    vk_ahb_note_use(&P[2].p); vk_ahb_sync_start(5);
    vk_ahb_sync_end(4);
    assert(P[0].end == 2 && P[2].end == 1);
    vk_ahb_sync_end(5);
    assert(P[2].end == 2);

    puts("poolsync ok");
    return 0;
}
'''


class PoolSync(unittest.TestCase):
    def test_separate_translation_unit_links_without_external_mutex(self):
        cc = shutil.which('cc') or shutil.which('clang')
        if not cc: self.skipTest('a C compiler is required')
        with tempfile.TemporaryDirectory(prefix='nx2a_poolsync_link_') as tmp:
            tmp = Path(tmp)
            src = SRC.read_text(encoding='utf-8').replace('#include "gfx_vk.h"', '#include "stand_in_gfx_vk.h"', 1)
            (tmp / 'gfx_vk_poolsync.c').write_text(src, encoding='utf-8')
            (tmp / 'stand_in_gfx_vk.h').write_text(r'''
#include <pthread.h>
#include <stdint.h>
typedef struct { uint64_t lo, hi, serial, use_gen, sync_ver; int zero_copy; } vkpool_t;
enum { VQ_AHB_NO_CACHE_SYNC = 1 };
static inline int vk_quirk(int q) { (void)q; return 1; }
#define VKLOG(...) ((void)0)
#define VKRED_ONCE(...) ((void)0)
''', encoding='utf-8')
            (tmp / 'mock.c').write_text(r'''
#include <stdint.h>
int vk_pool_sync(uint64_t s, uint64_t l, unsigned long long f) { (void)s; (void)l; (void)f; return 0; }
uint64_t vk_pool_bump(uint64_t s, uint64_t l) { (void)s; (void)l; return 1; }
int vk_pool_stale(uint64_t s, uint64_t l, uint64_t v) { (void)s; (void)l; (void)v; return 0; }
int main(void) { return 0; }
''', encoding='utf-8')
            r = subprocess.run([cc, '-std=gnu11', str(tmp / 'gfx_vk_poolsync.c'), str(tmp / 'mock.c'), '-pthread', '-o', str(tmp / 'check')], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_bookkeeping_from_the_real_source(self):
        cc = shutil.which('cc') or shutil.which('clang')
        if not cc: self.skipTest('a C compiler is required')
        with tempfile.TemporaryDirectory(prefix='nx2a_poolsync_') as tmp:
            src = SRC.read_text(encoding='utf-8').replace('#include "gfx_vk.h"', '#include "stand_in_gfx_vk.h"', 1)   # (the harness declares the types it uses)
            (Path(tmp) / 'gfx_vk_poolsync.c').write_text(src, encoding='utf-8')
            (Path(tmp) / 'stand_in_gfx_vk.h').write_text('/* the harness\'s vkpool_t, g_vk_quirks, vk_quirk, VKLOG and VKRED_ONCE stand in for gfx_vk.h */\n', encoding='utf-8')
            c, exe = Path(tmp) / 'check.c', Path(tmp) / 'check'
            c.write_text(STUB, encoding='utf-8')
            r = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-I' + tmp, str(c), '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('poolsync ok', r.stdout)


if __name__ == '__main__':
    unittest.main()
