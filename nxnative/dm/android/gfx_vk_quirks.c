// GPU driver quirks (gfx_vk_quirks.h): which GPU and driver the device has, and the table of workarounds for it.
// Add a row when a fix is needed by one vendor's hardware or one driver only; keep it off for the others until a device of theirs shows it is needed.
#include "gfx_vk.h"   // the Vulkan declarations, then gfx_vk_quirks.h
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { VENDOR_QUALCOMM = 0x5143, VENDOR_ARM = 0x13B5, VENDOR_IMG = 0x1010, VENDOR_SAMSUNG = 0x144D };

unsigned char g_vk_quirks[VQ_COUNT];
vk_gpu g_vk_gpu;

static int adreno(const vk_gpu *g) { return g->vendor == VENDOR_QUALCOMM; }   // both drivers: Qualcomm's own and Mesa's Turnip
static int mali(const vk_gpu *g) { return g->vendor == VENDOR_ARM || g->driver == VK_DRIVER_ID_ARM_PROPRIETARY; }   // Arm's own driver: Mali and Immortalis

static const struct {
    enum vk_quirk q; const char *name, *setting; int (*match)(const vk_gpu *); const char *why;
} ROWS[] = {
    // found with NEO's menu wipe and Xenoblade Chronicles 3's cloud dome
    {VQ_LRZ_TOUCH, "lrz_touch", "DM_VK_QUIRK_LRZ_TOUCH", adreno,
     "Adreno's low-resolution Z keeps depth from before a command-buffer cut or a pass switch: draws behind objects vanish"},
    // written for NieR:Automata
    {VQ_GS_SPLIT, "gs_split", "DM_VK_QUIRK_GS_SPLIT", adreno,
     "geometry-shader draws split into vertex and fragment passes: written and checked on Adreno only"},
    // the ghosting, smearing and stale vertices of Picross S+ on a Pixel 9 Pro's Mali-G715 (PICROSS_S_PLUS.md): a pool's pages are the game's own writes until the GPU reads
    // them, and what the GPU wrote until the CPU reads it back, and on Adreno the hardware buffer's mappings are cache-coherent, so nothing had to be done. Mali's are not:
    // the CPU's caches hold the pool's lines across a submission, and a game that writes guest memory and draws from it, or reads back what the GPU wrote, sees the
    // other side's old bytes. With this on, DMA_BUF_IOCTL_SYNC (start, then the cache's clean; end, then its invalidate) brackets every submission that uses a zero-copy pool
    // (gfx_vk_poolsync.c vk_ahb_sync_start / _end), which is the contract the AHardwareBuffer's dma-buf mapping asks for. Untested on a Mali device here (no Mali in reach): it is the fix for what that
    // report describes, and DM_VK_QUIRK_AHB_NO_CACHE_SYNC=0 turns it off to compare on one. It is off on every other driver: an Adreno mapping
    // was measured coherent, and an extra clean plus invalidate there costs an ioctl on every pool of every submission.
    {VQ_AHB_NO_CACHE_SYNC, "ahb_no_cache_sync", "DM_VK_QUIRK_AHB_NO_CACHE_SYNC", mali,
     "Mali keeps the CPU's cache over a hardware buffer's pages: the pool's pages are cleaned before and invalidated after each submission"},
};
_Static_assert(sizeof ROWS / sizeof *ROWS == VQ_COUNT, "one row a quirk");

const char *vk_driver_kind(void) {
    switch (g_vk_gpu.driver) {
    case VK_DRIVER_ID_QUALCOMM_PROPRIETARY: return "qualcomm";
    case VK_DRIVER_ID_MESA_TURNIP: return "turnip";
    case VK_DRIVER_ID_ARM_PROPRIETARY: return "mali";
    case VK_DRIVER_ID_MESA_PANVK: return "panvk";
    case VK_DRIVER_ID_SAMSUNG_PROPRIETARY: return "xclipse";
    case VK_DRIVER_ID_IMAGINATION_PROPRIETARY: return "powervr";
    default: return g_vk_gpu.vendor == VENDOR_QUALCOMM ? "adreno (driver not named)" : "unknown";
    }
}

void vk_quirks_init(VkPhysicalDevice pd, const VkPhysicalDeviceProperties *p) {
    vk_gpu *g = &g_vk_gpu; memset(g, 0, sizeof *g);
    g->vendor = p->vendorID; g->device = p->deviceID; g->version = p->driverVersion; g->api = p->apiVersion;
    if (VK_API_VERSION_MINOR(p->apiVersion) >= 2 || VK_API_VERSION_MAJOR(p->apiVersion) > 1) {   // VkPhysicalDeviceDriverProperties is core from Vulkan 1.2
        VkPhysicalDeviceDriverProperties dp = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES};
        VkPhysicalDeviceProperties2 p2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &dp};
        vkGetPhysicalDeviceProperties2(pd, &p2);
        g->driver = (int)dp.driverID; snprintf(g->name, sizeof g->name, "%s", dp.driverName);
    }
    char list[512]; size_t n = 0; list[0] = 0;
    for (size_t i = 0; i < sizeof ROWS / sizeof *ROWS; i++) {
        int on = ROWS[i].match(g); const char *e = getenv(ROWS[i].setting), *how = "";
        if (e && *e) { on = atoi(e) != 0; how = " (forced)"; }
        g_vk_quirks[ROWS[i].q] = (unsigned char)on;
        n += (size_t)snprintf(list + n, n < sizeof list ? sizeof list - n : 0, "%s%s %s%s", i ? ", " : "", ROWS[i].name, on ? "on" : "off", how);
        if (n >= sizeof list) n = sizeof list - 1;
    }
    VKLOG("driver %s (%s, id %d); quirks: %s", vk_driver_kind(), g->name[0] ? g->name : "no name", g->driver, list);
}
