// A zero-copy pool's pages against the CPU's cache (gfx_vk_quirks.h VQ_AHB_NO_CACHE_SYNC: Arm's Mali).
//
// A pool's pages are an AHardwareBuffer's, mapped into the process and imported as a VkBuffer, so the game's pointer is the GPU's memory. On Adreno those mappings are
// cache-coherent and nothing more is needed (probes/vkzc.c measured it). Mali's are not: the game's writes still sit in the CPU's cache when the GPU reads the pool, and
// the GPU's writes are not what the CPU reads back out of it. A game whose textures the CPU makes and the GPU draws (glyphs, UI, video frames), or that reads back what
// the GPU wrote, then sees the other side's old bytes: the washed-out picture and the lines and triangles of Picross S+ on a Pixel 9 Pro's Mali-G715 (PICROSS_S_PLUS.md),
// and the corruption a game shows only where its CPU touched a pool last.
// The dma-buf behind the buffer has an ioctl for exactly this: DMA_BUF_IOCTL_SYNC with DMA_BUF_SYNC_START ends the CPU's access (the kernel cleans the caches over the
// mapping) and DMA_BUF_SYNC_END gives it back (it invalidates them). So each submission brackets the pools it uses: START before it is queued, so the CPU's bytes reach
// memory before the GPU reads them; END once the GPU is past its fence, so its bytes reach the CPU. Only the pools the submission's command stream touched are bracketed
// (vk_pool_of notes the first use), not every pool alive.
//
// Two things keep an END from throwing away writes the GPU has not seen yet. Each entry is ENDed only once the fence of its own submission has passed (the completion thread
// names that fence, so a submission queued later waits for its own), and only if the pool has not been synced again since (vk_pool_bump's version: a START after the entry
// was noted moves it past). A pool freed in between has no entry to find any more, and is skipped.
//
// Lock order with gfx_vk_core.c: g_ahb_mu, then g_pool_mu. The three functions below that the core defines take g_pool_mu only and are called with g_ahb_mu held; no path of
// the core's takes g_ahb_mu (a pool made or dropped, or a hardware buffer's pages substituted, never calls into this file), so the two orders cannot meet.
#include "gfx_vk.h"
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

extern pthread_mutex_t g_ahb_mu;   // gfx_vk_core.c

// gfx_vk_core.c (the pool table is its own): the dma-buf of the pool noted, synced with the flags (START cleans the CPU's caches over it, END invalidates them); the pool's
// version past the one noted; 1 when that version is past, or the pool is gone
int vk_pool_sync(uint64_t serial, uint64_t lo, unsigned long long flags);
uint64_t vk_pool_bump(uint64_t serial, uint64_t lo);                        // (g_ahb_mu held: called with it)
int vk_pool_stale(uint64_t serial, uint64_t lo, uint64_t ver);

// <linux/dma-buf.h>: DMA_BUF_SYNC_READ 1, WRITE 2, RW 3, START 4, END 8 (the ioctl's direction is whoever asks: START ends the CPU's access, END gives it back)
enum { DMA_BUF_SYNC_READ = 1, DMA_BUF_SYNC_WRITE = 2, DMA_BUF_SYNC_RW = DMA_BUF_SYNC_READ | DMA_BUF_SYNC_WRITE,
       DMA_BUF_SYNC_START = 4, DMA_BUF_SYNC_END = 8 };
typedef struct { uint64_t serial, base, ver, fence; } ahbuse_t;   // a pool a submission uses: its serial and address, the version its END is for, that submission's fence
static struct { ahbuse_t *v; int n, cap, head, cur; uint64_t gen; } g_use;
// g_use.v[head, n): STARTed and waiting for their fence (an END passes head on); g_use.v[cur, n): noted since the last START, which has not run for them yet. A START
// brackets the submissions between two queue submits, so it must not clean a pool a submission already waiting for its fence covers again: it takes [cur, n) only
static int sync_on(void) {
    static int v = -1;
    if (v < 0) { const char *e = getenv("DM_VK_QUIRK_AHB_NO_CACHE_SYNC"); v = e && *e ? atoi(e) : vk_quirk(VQ_AHB_NO_CACHE_SYNC); }
    return v;
}
// A submission's first use of pool p: the second use in it costs one comparison.
void vk_ahb_note_use(vkpool_t *p) {
    if (!sync_on() || !p->zero_copy) return;
    if (!g_use.gen) g_use.gen = 1;   // generation 0 is every new pool's use_gen: never taken for this submission's
    if (__atomic_load_n(&p->use_gen, __ATOMIC_RELAXED) == g_use.gen) return;
    pthread_mutex_lock(&g_ahb_mu);
    // (a pool a submission still waiting for its fence used can be noted again: the game wrote into it since, and this submission's START cleans it; the earlier entry's END
    //  passes on it by version, which that START moved past)
    if (p->use_gen != g_use.gen && g_use.n < 65536) {
        if (g_use.n == g_use.cap) { int c = g_use.cap ? g_use.cap * 2 : 64; void *q = realloc(g_use.v, (size_t)c * sizeof *g_use.v);
            if (!q) { pthread_mutex_unlock(&g_ahb_mu); return; } g_use.v = q; g_use.cap = c; }
        p->use_gen = g_use.gen; g_use.v[g_use.n].serial = p->serial; g_use.v[g_use.n].base = p->lo; g_use.v[g_use.n].ver = 0; g_use.v[g_use.n].fence = 0; g_use.n++;
    }
    pthread_mutex_unlock(&g_ahb_mu);
}
// Called as a submission's replay ends and again just before its queue submit (the game may have written pools in between). The pools noted since the last call are cleaned,
// so the CPU's writes reach memory before the GPU reads them, and their version goes up: the GPU's writes from before are not the CPU's any more, so an earlier END must not
// invalidate the lines that hold what the game wrote.
void vk_ahb_sync_start(uint64_t fence) {
    if (g_use.n == g_use.cur) return;
    pthread_mutex_lock(&g_ahb_mu);
    int bad = 0;
    for (int i = g_use.cur; i < g_use.n; i++) {
        if (vk_pool_sync(g_use.v[i].serial, g_use.v[i].base, DMA_BUF_SYNC_START | DMA_BUF_SYNC_RW)) bad++;
        g_use.v[i].ver = vk_pool_bump(g_use.v[i].serial, g_use.v[i].base);   // (the version this END is for: a later submission's START moves past it)
        g_use.v[i].fence = fence;
    }
    g_use.cur = g_use.n; g_use.gen++;
    pthread_mutex_unlock(&g_ahb_mu);
    if (bad) VKRED_ONCE(ahb_sync, "%d zero-copy pools' dma-bufs could not be synced (DMA_BUF_IOCTL_SYNC): the GPU may read what the CPU's cache still holds", bad);
    static int shown; if (!shown++) VKLOG("zero-copy pools: the CPU's cache is cleaned before each submission and invalidated after it (DM_VK_QUIRK_AHB_NO_CACHE_SYNC=0 turns it off)");
}
// The GPU is past submission `fence` (the completion thread's): the pools it used that no later submission has written into are the CPU's again.
void vk_ahb_sync_end(uint64_t fence) {
    if (g_use.n == g_use.head) return;
    pthread_mutex_lock(&g_ahb_mu);
    while (g_use.head < g_use.n && g_use.v[g_use.head].fence <= fence) {   // (the queue is in fence order: the completions come in that order too)
        ahbuse_t e = g_use.v[g_use.head++];
        if (!e.ver || vk_pool_stale(e.serial, e.base, e.ver)) continue;   // (written again since, or freed: nothing to invalidate)
        vk_pool_sync(e.serial, e.base, DMA_BUF_SYNC_END | DMA_BUF_SYNC_RW);
    }
    if (g_use.head == g_use.n) { g_use.head = g_use.cur = g_use.n = 0; }
    else if (g_use.head > 4096) {   // (the front moves: what is still waiting goes back to the start of the array)
        memmove(g_use.v, g_use.v + g_use.head, (size_t)(g_use.n - g_use.head) * sizeof *g_use.v);
        g_use.n -= g_use.head; g_use.cur = g_use.cur > g_use.head ? g_use.cur - g_use.head : 0; g_use.head = 0;
    }
    pthread_mutex_unlock(&g_ahb_mu);
}
