# Sonic Frontiers shader extraction

Title ID: `01004AD014BF0000`. Android bring-up is experimental; a successful
shader split does not establish SDK compatibility or runtime playability.

## Zero shaders at Stage 5

The reported base v1.0.0 build stopped with `shaders: 0` and many unmatched
`main control section` messages. The splitter previously had no reader for
Needle's `HNSHV002` containers. Its raw-main fallback requires pointer records
or address-building patterns that do not describe these containers.

The Needle reader is backported from the user-supplied branch:

- https://github.com/mymes1/droidplay-core/tree/arena%2F16159649-droidplay-core
- Relevant fix: https://github.com/mymes1/droidplay-core/commit/5b34f894d3be710be011b61d6fa7fe35a7c2f7df
- Source: `dm/gfx/tools/shader_split.py`, `dm/gfx/tests/test_needle.py`

It reads bounded HNSHV002 archives embedded in main or scanned RomFS files,
including HHNEEDLE shader-file wrappers such as Frontiers' NeedleShader.pac.
NVN records can have a 0x90 descriptor, a different self-consistent descriptor
size, or a control section directly followed by code. Descriptor offsets and
lengths must fit the containing archive; existing shader-stage and control/code
length validation still applies. Unreadable sections are reported without
throwing away other readable sections in the file. If no section is readable,
the file still fails. Independent vertex programs use the existing separable
pixel-input-cover pairing; arbitrary vertex/pixel pairs are not invented.

The normal blob scan now runs the reader before the raw-main fallback. It
therefore also excludes successfully extracted controls from raw rediscovery.
No zero-shader guard has been removed and no shader or game data is bundled.

## Verification and remaining work

Synthetic tests in `tests/test_shader_needle.py` cover embedded vertex/pixel/
compute variants, alternate and broken descriptors, descriptorless payloads,
HHNEEDLE wrappers, bounds and partially unreadable files. The existing ADRP,
CPK, spill, pool, mapping, CMP and BND3 regressions also pass (42 tests total).

The user's NSP is not available in the development environment. Repeat the
full port with an APK containing this change and check Stage 5's shader count.
This backport addresses a missing reader and the specific Frontiers layout
fix in the supplied branch; it does not prove all of the game's material
shaders are extracted, that compressed outer containers are supported, or
that later translation/runtime stages work. If the build still stops, share
the log bundle including `shader_split.log` and survey metadata.

## Mali-G57 startup / copied-pool pressure

A subsequent device log reaches `nnMain`, Vulkan initialization and
`WindowInitialize` (1280x720). It reports 1792 MiB of copied GPU pools after
zero-copy fails with -1. There is no fatal signal, allocation error or Android
exit record in that excerpt; it does not establish the cause of termination.

Previously the first submission uploaded every copied pool and allocated a
pool-sized merge baseline for each, including pools never bound to the GPU.
The fallback could therefore add another 1792 MiB of baselines as well as
reading/touching all the guest and shadow pages. Synchronization now skips
never-GPU-used pools. `vk_pool_of` records first use even when a CPU lookup
primed its cache; first GPU use still uploads the complete current contents.
Once used, pools keep the existing dirty tracking, cache flush/invalidation,
and independent CPU/GPU-write merge behavior. This reduces wasted work and
baseline memory for unused chunks, not the driver's initial buffer allocation
or the game's reserved heap. Savings and frame rate require device measurement.

Zero-copy failure stages are now logged once per stage without requiring
`DM_VK_MEM_DIAG`. Shared device diagnostics include recent Android player-process
exit records (API 30+) so low-memory kills and native crashes can be distinguished.
After rebuilding the runtime, share the runtime ZIP, including `device.txt`, if
startup still ends abruptly. Do not force zero-copy or disable Mali cache sync
to hide this failure: neither has been validated on this device.


## Black terrain / models: material-flag rounding

The supplied droidplay-core branch contains no title profile or `patches.py`
for `01004AD014BF0000`. Its relevant fixes are shared renderer/extractor changes,
not build-ID-specific instructions to patch into the game's executable:

- `e93661d0fadd2e4c7a61075f73bcf9ea0ef03c14`: round fragment UNORM8 outputs
  to even to preserve Hedgehog Engine 2's packed G-buffer material flags.
- `57a57d6f84bb8173a5bdf821e95e247876dc4370`: enable that correction on
  Mali as well as Adreno (not just Qualcomm's vendor ID).

These are adapted to this repo's Vulkan shader-module variants. The pass's
R8, RG8, RGBA8 and BGRA8 UNORM attachments select which output locations to
round; float, integer, SNORM and sRGB attachments are excluded. The shader
variant cache includes that mask. The pipeline prewarm envelope advances to
PPE4 so old pipeline recipes cannot masquerade as the new layout/semantics.
`DM_VK_UNORM8_RNE=0` at startup disables it for comparison; the default is on,
including Mali-G57. No game shader bundle retranslation is needed: the change
runs when the runtime creates fragment shader modules.

Important correction to the reference implementation: SPIR-V core opcode 46
is **OpConstantNull**, not RoundEven. The backport emits **OpExtInst** using
**GLSL.std.450 RoundEven (instruction 2)**, imports the instruction set when
needed, and updates ID bounds and instruction lengths. Existing imports are
reused. Output pointer reads, copies and access-chain aliases are conservatively
left unchanged instead of rewriting them unsafely.

`tests/test_spv_rne.py` checks the math, target formats, disable switch, valid
instruction emission and imports, and the real runtime variant-cache function.
The generated scalar and vector fragment modules pass Khronos `spirv-val`
for Vulkan 1.1. The combined RNE, indirect-compute, pipeline-cache, copy-back
and pool-sync run has 28 passing tests and one optional game-SPIR-V corpus test
skipped (no game data). No Frontiers/Mali device run is available here: this
addresses the reference branch's material-flag rounding issue, not every
possible black-object/glitch cause, and does not establish successful startup.

## Confirmed low-memory exits on the 4 GB Tab A9

`Sonic_Frontiers-log.zip` supplied in the branch records two player exits with
Android reason 3 (`ApplicationExitInfo.REASON_LOW_MEMORY`), on an SM-X110 with
3740 MB usable RAM. Both runs report `mmap-temporary: errno=13` (EACCES).
The latest ends while registering the large copied pools; the previous gets
as far as loading the 2308-shader bundle and creating render targets. This is
now evidence of Android low-memory termination, not a shader compilation
crash or a missing shader bundle.

The earlier unused-pool snapshot optimization did not defer the initial Vulkan
allocations. `gfx_vk_poolcopy.inc` now defers copied-pool backing on Mali until
`vk_pool_of` first needs the GPU buffer (`lazy=3`). CPU address lookups leave
it unbacked. First use allocates and fully uploads it, creates its merge baseline,
flushes non-coherent memory and enables submission readback; existing cached
and coherent allocation fallback rules remain. This also fixes eager copied
pools first bound after begin_segment's initial upload scan: they must be
seeded before that submission can use them. Pending pools can be removed
without allocating anything. `DM_VK_COPY_LAZY=0` restores eager allocation;
`=1` enables deferral on other vendors.

After an mmap EACCES/EPERM, later registrations use the copied path without
repeatedly allocating hardware buffers to try the same denied mapping. Other
errors (including allocation failures) do not globally disable zero-copy.
No permission checks are bypassed, guest pages remapped, or heap sizes guessed
smaller. Deferral reduces duplicated allocations for pools/chunks the GPU does
not use; a 256 MB chunk actually used still costs a 256 MB GPU allocation and
baseline. This does not guarantee Frontiers will fit within this tablet's RAM.

Regression harnesses compile the real registration and copied-backing helpers,
check no allocations at deferred registration, current guest bytes uploaded
at first use, cache flush and baseline publication, no duplicate allocation,
stale identity rejection, late eager first use, cached-to-coherent fallback,
and vendor/override policy. A separate harness checks permission-denial memory.


## Copied-pool CPU read performance on Mali

Copied pools now prefer HOST_CACHED memory on Mali. The previous coherent-only
allocation policy did not require CPU caching, although submission readback
scans the mapped GPU copy against its baseline. Large pools in uncached memory
can therefore impose substantial CPU read costs. This is a performance fix
candidate for the reported very slow gameplay, not a measured device speedup.

Non-coherent cached allocations retain the existing atom-aligned upload flushes
and post-fence readback invalidations. Allocation failures or unavailable cache
operations retain the coherent fallback; device loss remains fatal. Other GPU
vendors keep their existing default. `DM_VK_POOL_CACHED=0` restores the old Mali
allocation policy and `=1` explicitly requests cached memory on any vendor.
The deferred-backing memory fix remains enabled; no synchronization is skipped
and no additional whole-pool buffers are introduced.

The production-helper regression covers both vendor defaults, explicit on/off,
missing cache operations and allocation fallback. Device profiling and another
Frontiers run are still needed to establish how much this improves frame time;
memory pressure and GPU workload can independently limit this 4 GB tablet.
