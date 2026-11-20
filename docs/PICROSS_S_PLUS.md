# Picross S+ 1.0.1: RP6 validation

Title ID: `0100D7B01AE58000`, update `v65536`, SDK 16.2.0. Tested with the
v0.3.0 runtime on Retroid Pocket 6, Android 13, Adreno 740 and manually selected
PurpleVK-public git-62ac221a33. Menu, puzzle rendering and controls work; the
tester completed a puzzle without issues. Full playthrough and all DLC are unverified.

## Startup fix

The generated profile exhausted its 4.38 GiB alias region: eight
`AllocateAddressRegion` failures (`0x1803`) preceded a SIGSEGV in `h_nvnBufferMap`.
The existing porter's `alias_headroom(profile, 8)` operation expands `mem_alias`
from `0x118000000` to `0x318000000`, and `mem_reserve` from `0x140000000` to
`0x340000000`. `app_pool` stays `0xCD500000`: this reserves virtual addresses,
not another 8 GiB of physical RAM. The test reached address extents beyond the
old limit and booted without those allocation failures. A 142-second capture
had no allocation failures or crash reports; subsequent manual puzzle play passed.
No NVN implementation or game binary patch is included.

## Limits

Pixel 9 Pro with system Mali-G715 also boots with the expanded profile, but has
persistent washed-out rendering and lines/triangles on updates. A separate
`DM_VK_NO_ZEROCOPY=1` diagnostic test removed the visible glitches according to
the tester, but was far too slow. That is the clue the runtime now follows: with
every pool *copied* instead of zero-copy, the game's data reaches the GPU through
the copy the host itself makes, so whatever the game writes before a submission is
there; with zero-copy pools the pages are the game's own, and Mali's mapping of a
hardware buffer is not cache-coherent with the CPU. The runtime now brackets each
submission's zero-copy pools with `DMA_BUF_IOCTL_SYNC` (`nxnative/dm/android/gfx_vk_poolsync.c`,
the `ahb_no_cache_sync` quirk, on for Arm's driver): the CPU's cache is cleaned
before the GPU reads those pages and invalidated once it has written them, which is
the same data the copy path delivered, without the copy. That is the fix this
document points at, not a measured result: it has not been run on a Mali device
here. On a Pixel 9 Pro the comparison is
`DM_VK_QUIRK_AHB_NO_CACHE_SYNC=1` (the new default of the row there) against
`DM_VK_QUIRK_AHB_NO_CACHE_SYNC=0`, with `DM_VK_NO_ZEROCOPY=1` as the slow reference.
The profile still sets neither option. Pixel 9a has not been tested.
Stock-driver RP6 behavior is not established. This contribution claims RP6 /
PurpleVK support only.

## Review checklist

- [x] RP6 menu and puzzle manually verified
- [x] Address exhaustion reproduced and controlled headroom test passed
- [x] Real RP6 gameplay screenshot and README games-table cell added
- [ ] Retest this profile on current main (device test used v0.3.0 runtime)

No APKs, game data, drivers, keys, private FPS menu or Pixel diagnostics are included.
