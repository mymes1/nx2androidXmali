# Sonic Colors: Ultimate on Mali: bring-up notes

Title ID `010040E0116B8000`, SEGA / Blind Squirrel, Switch release 2021-09-07.
The bring-up device is a **Samsung Galaxy Tab A9 (SM-X110)**: Helio G99, **Mali-G57 MC2**,
Android 13, 4 GB of RAM (an 8 GB model exists), Arm's proprietary Vulkan driver.

This document is the state of the port, not a finished profile: there is no `android.tsv`
for the game yet, and a profile cannot be written from anything but the game's own files
(`nxnative/dm/games/<title ID>/game.tsv` needs each module's build ID, which only a dump
has). What follows is what is known, what the runtime now does for Mali, and the steps that
remain.

## What the 0.4.0-alpha02 failure was

```
FileNotFoundError: .../ExeFS/sdk
The port ended without an artifact
```

The survey (`nso_syms.py`) read the ExeFS's SDK module by name. Sonic Colors: Ultimate ships
`main` and `subsdk0..subsdk3` and **no `sdk` module**: its NintendoSDK code is in the subsdks,
in this build's own layout. The survey now lists the modules it finds, and when there is no
`sdk` it says so in the report instead of dying (`nxnative/dm/tools/nso_syms.py`
`exefs_modules` / `sdk_module` / `exports_or_empty`; `tests/test_porter.py`
`test_survey_without_an_sdk_module`), and the rest of the port runs on that ExeFS like on any
other. Nothing in the host needs a module called `sdk`: `game.tsv`'s `modules` row names the
modules to load, and the guess (`dm/games/profile_guess.py`) keeps every subsdk main imports
from.

The port is run again the same way:

```
python -m porter inspect "<game folder>"                     # the survey, again, now with the module list
python -m porter port "<game folder>" --experimental --debug -k <prod.keys>
```

If an earlier `010040E0116B8000_survey` / `<title ID>_build/profile` is still there from the
failed run, delete it so the survey is made again.

## What Mali needs from the runtime (now in it)

Two things, both vendor-keyed, none of them per game (AGENTS.md §4):

1. **BC textures are decoded on the CPU.** The Switch's textures are BC-compressed, and Arm's
   Mali drivers do not sample BC. `has_bc` is false there, so `gfx_vk_tex.c bc_decode` expands
   each block as it is uploaded. A profile that says `requires bcn` used to make the installer
   refuse the device; it now says what will happen and installs (`porter/install.py`).
   Expect 4 to 8 times the texture memory, and a slower first use of each texture.
2. **Cache maintenance over zero-copy pools.** A pool is an `AHardwareBuffer` mapped into the
   process and imported as a `VkBuffer`, so the game's own pages are the GPU's memory. Adreno's
   mappings are cache-coherent and need nothing; Mali's are not, and the game then draws the
   CPU's stale lines (washed-out picture, lines and triangles: Picross S+ on a Mali-G715,
   [PICROSS_S_PLUS.md](PICROSS_S_PLUS.md)). `nxnative/dm/android/gfx_vk_poolsync.c` brackets
   each submission with `DMA_BUF_IOCTL_SYNC` START (clean) and END (invalidate) on the dma-bufs
   of the pools that submission touched, giving each pool back only after its own fence and only
   if the game has not written into it since; the quirk row is `ahb_no_cache_sync`
   (`DM_VK_QUIRK_AHB_NO_CACHE_SYNC`), on for Arm's driver and off elsewhere. The bookkeeping is
   checked by `tests/test_vk_pool_sync.py`, which compiles the real file (`python -m unittest
   tests.test_vk_pool_sync`); only a Mali device can show that the ioctl itself does the job.

A Galaxy Tab A9 can also pin the Mali GPU's clock through devfreq (`DM_GPU_TURBO`), but the
node is root-owned: without root the host logs one `[RED]` line and leaves the governor alone.

The Tab A9's 4 GB is the other real constraint. Pools are backed lazily where the CPU reads
hardware buffers slowly (`DM_VK_ZEROCOPY_LAZY`), and a port of a 2021 console game should be
tried with `env DM_VK_ZEROCOPY_LAZY=1` in its `android.tsv` if Android's low-memory killer ends
the game.

## What still needs the device

These are the steps, in order, once the port produces a pack:

1. Import the profile package, install the pack, open the game, and read the first lines of the
   log: `[vk] driver mali (...)` with `ahb_no_cache_sync on`, and
   `zero-copy pools: the CPU's cache is cleaned …`. A `[RED]` line about
   `DMA_BUF_IOCTL_SYNC` means the buffer's fd could not be synced, which is worth reporting.
2. If the picture is right and the frame rate is not, measure before tuning:
   `DM_RENDER_SCALE=0.75` and `DM_DYN_SCALE=1` are the two switches that matter on a G57 MC2's
   bandwidth, and `DM_GPU_TURBO=1` only helps on a rooted tablet.
3. If the game crashes with `[mem]` lines, it is the profile's memory keys (`game.tsv`), not
   Mali: the guess's `mem_reserve` / `mem_alias` follow the NPDM's system resource and the
   heap the game asks for.
4. When it boots and plays, the profile moves into `nxnative/dm/games/010040E0116B8000/` with an
   `android.tsv` (`status`, `tested`), and its line in
   [PORT_CHECKLIST.md](PORT_CHECKLIST.md) is ticked
   ([PORTING_GUIDE.md](PORTING_GUIDE.md) steps 4 and 5).

Nothing in this document claims the game runs on the tablet yet: the port has to be run on the
player's own dump, which is not in this workspace.
