// Copied pools on low-memory devices: allocate at first GPU lookup, not NVN
// registration. No guest pages are remapped. CPU-only pools stay anonymous.
static int copy_lazy_on(void) {
    static int on = -1;
    if (on < 0) { const char *e = getenv("DM_VK_COPY_LAZY"); on = e ? atoi(e) != 0 : V.props.vendorID == 0x13b5; }
    return on;
}
static void copy_pool_alloc(vkpool_t *p, uint64_t size) {
    // The copy-back merge reads this mapping on the CPU every submission.
    // HOST_COHERENT alone can select uncached Mali memory: GiB-scale scans
    // then run at device-memory read speed. Prefer cached RAM on Mali, keeping
    // the explicit flush/invalidate path and the coherent fallback below.
    const char *e = getenv("DM_VK_POOL_CACHED");
    int cached = e ? atoi(e) != 0 : V.props.vendorID == 0x13b5;
    VkResult r = VK_ERROR_FEATURE_NOT_PRESENT;
    if (cached && vkFlushMappedMemoryRanges && vkInvalidateMappedMemoryRanges) r = pool_copied_alloc(p, size, 1);
    if (r == VK_ERROR_DEVICE_LOST) VKCHK(r);
    if (r) VKCHK(pool_copied_alloc(p, size, 0));
}
// Registration can precede a submission's first binding. begin_segment's upload
// has already happened then: seed new GPU storage before returning its handle.
static void copy_pool_seed(vkpool_t *p) {
    if (!p->shadow || p->synced) return;
    uint64_t size = p->hi - p->lo, stamp = nvn_dirty_now();
    uint8_t *lo = (uint8_t *)(uintptr_t)p->lo;
    if (!p->baseline) p->baseline = malloc(size);
    if (!p->baseline) die("[vk] copied-pool baseline allocation failed (%llu bytes)", (unsigned long long)size);
    p->span = nvn_dirty_span(lo, size);
    if (p->span) nvn_dirty_protect(lo, size);
    memcpy(p->shadow, lo, size); memcpy(p->baseline, p->shadow, size);
    pool_cache_sync(p, 0, 0, size);
    p->stamp0 = p->stamp = stamp; p->baseline_complete = 0; p->synced = 1;
    g_shadow_pools = 1;   // first use may follow begin_segment's shadow scan
}
static int copy_pool_back(vkpool_t *p, uint64_t serial) {
    pthread_mutex_lock(&g_lazy_mu);   // pool removal/movement also takes this lock
    pthread_rwlock_rdlock(&g_pool_mu);
    int st = p - g_pool < g_npool && p->serial == serial ? p->lazy : -1;
    uint64_t lo = p->lo, size = p->hi - p->lo;
    pthread_rwlock_unlock(&g_pool_mu);
    if (st == 3) {
        vkpool_t q = {0}; q.lo = lo; q.hi = lo + size;
        copy_pool_alloc(&q, size);
        copy_pool_seed(&q);
        pthread_rwlock_wrlock(&g_pool_mu);
        p->buf = q.buf; p->mem = q.mem; p->shadow = q.shadow; p->noncoherent = q.noncoherent;
        p->baseline = q.baseline; p->span = q.span; p->synced = q.synced;
        p->stamp0 = q.stamp0; p->stamp = q.stamp;
        __atomic_store_n(&p->lazy, 0, __ATOMIC_RELEASE);
        __atomic_fetch_add(&g_pool_gen, 1, __ATOMIC_RELEASE);
        pthread_rwlock_unlock(&g_pool_mu);
        __atomic_fetch_add(&g_cp_bytes, size, __ATOMIC_RELAXED);
        VKLOG("copied pool %#llx: %.1f MB backed and uploaded at first GPU use", (unsigned long long)lo, size / 1048576.0);
    }
    pthread_mutex_unlock(&g_lazy_mu);
    return st < 0 ? 1 : 0;
}
