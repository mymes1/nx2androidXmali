"""Exercise the original SDK function lookup with and without a separate sdk module."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import csrc


@unittest.skipUnless(shutil.which('cc'), 'C compiler required')
class SdkOriginal(unittest.TestCase):
    def test_allocator_and_address_region_lookup(self):
        src = csrc.read('nxnative/dm/host/hle_os.c')
        parts = [csrc.function(src, head) for head in (
            'static void *sdk_original(', 'static void sa_init(void)',
            'static void ar_init(void)', 'static void sa_init_find(void)')]
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
typedef struct { const char *name; int id; } module_t;
static module_t g_mod[3]; static int g_nmod;
static int sdk_marker, main_marker, subsdk_marker;
static int fallbacks, omit_aligned;
static void *module_sym(module_t *m, const char *name) {
    if (omit_aligned && strstr(name, "8AllocateEmm")) return NULL;
    if (!strstr(name, "StandardAllocator") && !strstr(name, "AddressRegion")) return NULL;
    return m->id == 1 ? &sdk_marker : m->id == 2 ? &main_marker : &subsdk_marker;
}
static void *guest_sym(const char *name) {
    fallbacks++;
    /* Model loader's loaded-module order, with symbols in subsdk2. */
    return module_sym(&g_mod[1], name);
}
#define DM_GAME_ALLOC_BIG_ALIGN 0
#define LOG(...) ((void)0)
#define die(...) abort()
typedef int Result;
static void *(*g_sa_alloc)(void *, size_t), *(*g_sa_alloc_al)(void *, size_t, size_t);
static int g_sa_big_align;
static Result (*g_ar_alloc)(uint64_t *, size_t), (*g_ar_free)(uint64_t);
static void (*g_sa_init)(void *, void *, size_t), (*g_sa_init_b)(void *, void *, size_t, bool);
''' + '\n'.join(parts) + r'''
int main(void) {
    g_nmod = 2; g_mod[0] = (module_t){"main", 2}; g_mod[1] = (module_t){"subsdk2", 3};
    sa_init_find(); sa_init(); ar_init();
    assert((void *)g_sa_init_b == &subsdk_marker && (void *)g_sa_init == &subsdk_marker);
    assert((void *)g_sa_alloc == &subsdk_marker && (void *)g_ar_alloc == &subsdk_marker);
    assert((void *)g_ar_free == &subsdk_marker && fallbacks == 6);
    omit_aligned = 1; sa_init(); assert(g_sa_alloc && !g_sa_alloc_al);
    g_mod[2] = (module_t){"sdk", 1}; g_nmod = 3; fallbacks = 0; omit_aligned = 0;
    sa_init_find(); sa_init(); ar_init();
    assert((void *)g_sa_init_b == &sdk_marker && (void *)g_sa_alloc == &sdk_marker);
    assert((void *)g_ar_free == &sdk_marker && fallbacks == 0);
    assert(!sdk_original("missing"));
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='nx2a-sdk-lookup-') as tmp:
            path = Path(tmp)
            (path / 'test.c').write_text(harness, encoding='utf-8')
            result = subprocess.run(['cc', '-std=gnu11', str(path / 'test.c'), '-o', str(path / 'test')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(path / 'test')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__': unittest.main()
